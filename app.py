import streamlit as st
import pandas as pd
import pdfplumber
import io
import re
from dateutil import parser as date_parser
from pdfminer.pdfdocument import PDFPasswordIncorrect

def extract_remarks(details):
    s = str(details).strip()
    u = s.upper()
    
    # 1. Standard Banking Categories
    if any(k in u for k in ['ATM WDL', 'ATM CASH', 'ATMCARD']): return 'Atm cash withdrawal'
    if any(k in u for k in ['INT.PD', 'INTEREST CREDIT', 'INT CREDIT']): return 'INTEREST'
    if any(k in u for k in ['SMS CHARGE', 'CHARGES', 'FEE']): return 'Bank Charges'
    if 'LIC OF' in u or 'LIC' in u: return 'LIC'
    if any(k in u for k in ['GST', 'CBDT', 'INCOME TAX', 'TAX']): return 'GST / Tax Paid'
    if 'CHEQUE' in u or 'CLG' in u:
        if 'CLEARING' in u: return 'Cheque Deposited'
        if 'WITHDRAWAL' in u: return 'Cash Withdrawals'
        return 'Cheque Paid'

    # 2. NEFT / IMPS / RTGS Pattern Extraction (Enclosed in asterisks *NAME*)
    if '*' in s:
        asterisk_parts = s.split('*')
        for p in asterisk_parts:
            p_strip = p.strip()
            p_clean = re.sub(r'[^a-zA-Z\s]', '', p_strip).strip()
            if (len(p_clean) >= 3 
                and not re.search(r'^[A-Z]{4}0', p_strip)
                and not p_strip.startswith('ICIN')
                and not any(k in p_clean.upper() for k in ['NEFT', 'IMPS', 'RTGS', 'DEP', 'TFR', 'BARB', 'ICIC', 'HDFC', 'SBIN', 'BATCHID', 'SALABATPURA', 'UDHASURAT'])):
                return p_clean.title()

    # 3. UPI Pattern Extraction
    if 'UPI' in u:
        parts = [p.strip() for p in s.split('/') if p.strip()]
        for p in parts:
            p_clean = re.sub(r'[^a-zA-Z\s]', '', p).strip()
            if len(p_clean) >= 3 and not any(k in p_clean.upper() for k in ['UPI', 'PAID VIA', 'GPAY', 'PAYTM', 'OKAXIS', 'OKICIC', 'OKHDFC', 'YBL']):
                return p_clean.title()
        return 'UPI'

    # 4. Fallback for NEFT / IMPS / RTGS
    if 'NEFT' in u: return 'NEFT'
    if 'IMPS' in u: return 'IMPS'
    if 'RTGS' in u: return 'RTGS'
    if 'WDL TFR' in u or 'WITHDRAWAL' in u: return 'Withdrawals'

    return 'Other'

def clean_number(val_str):
    if not val_str:
        return 0.0
    s = str(val_str).replace(',', '').strip()
    if not s or s == '-':
        return 0.0
    is_dr = 'DR' in s.upper()
    match = re.search(r'\d+(?:\.\d+)?', s)
    if match:
        try:
            val = float(match.group(0))
            return -val if is_dr else val
        except ValueError:
            return 0.0
    return 0.0

def parse_pdf(pdf_file, password=''):
    all_rows = []
    
    # Strategy 1: Table Extraction (For Grid-based PDFs like SBI, Kotak, HDFC)
    with pdfplumber.open(pdf_file, password=password) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables() or []
            for table in tables:
                if not table: continue
                
                header_found = False
                inferred_col_map = False
                col_map = {'date': -1, 'details': -1, 'debit': -1, 'credit': -1, 'balance': -1}
                
                for row in table:
                    cleaned_row = [str(cell).replace('\n', ' ').strip() if cell else '' for cell in row]
                    
                    if not header_found and not inferred_col_map:
                        row_upper = [c.upper() for c in cleaned_row]
                        date_idx = details_idx = debit_idx = credit_idx = bal_idx = -1
                        
                        for i, cell in enumerate(row_upper):
                            if any(k in cell for k in ['DATE', 'TRAN DATE', 'VALUE DATE']) and date_idx == -1: date_idx = i
                            elif any(k in cell for k in ['DETAILS', 'NARRATION', 'DESCRIPTION', 'PARTICULARS']) and details_idx == -1: details_idx = i
                            elif any(k in cell for k in ['DEBIT', 'WITHDRAWAL', 'DR']) and debit_idx == -1: debit_idx = i
                            elif any(k in cell for k in ['CREDIT', 'DEPOSIT', 'CR']) and credit_idx == -1: credit_idx = i
                            elif 'BALANCE' in cell and bal_idx == -1: bal_idx = i
                            
                        if date_idx != -1 and details_idx != -1 and (credit_idx != -1 or debit_idx != -1):
                            col_map['date'] = date_idx
                            col_map['details'] = details_idx
                            col_map['debit'] = debit_idx
                            col_map['credit'] = credit_idx
                            col_map['balance'] = bal_idx
                            header_found = True
                            continue
                            
                        for i, cell in enumerate(cleaned_row):
                            if re.search(r'\d{1,2}[-/ \.]+[A-Za-z0-9]{2,}[-/ \.]+\d{2,4}', cell):
                                date_idx = i
                                break
                                
                        if date_idx != -1:
                            amt_cols = []
                            for i in range(date_idx + 1, len(cleaned_row)):
                                cell_clean = cleaned_row[i].replace(' ', '').replace(',', '')
                                if re.match(r'^-?\d+\.\d{2}(?:[CcDd][Rr])?$', cell_clean):
                                    amt_cols.append(i)
                                    
                            if len(amt_cols) >= 2:
                                col_map['date'] = date_idx
                                col_map['balance'] = amt_cols[-1]
                                if len(amt_cols) >= 3:
                                    col_map['credit'] = amt_cols[-2]
                                    col_map['debit'] = amt_cols[-3]
                                else:
                                    col_map['debit'] = amt_cols[-2]
                                    col_map['credit'] = -1
                                    
                                max_len = -1
                                det_idx = -1
                                for i in range(date_idx + 1, amt_cols[-2]):
                                    if len(cleaned_row[i]) > max_len:
                                        max_len = len(cleaned_row[i])
                                        det_idx = i
                                col_map['details'] = det_idx if det_idx != -1 else date_idx + 1
                                inferred_col_map = True
                            else:
                                continue
                        else:
                            continue
                            
                    if header_found or inferred_col_map:
                        date_str = cleaned_row[col_map['date']] if col_map['date'] != -1 and col_map['date'] < len(cleaned_row) else ''
                        if not date_str: continue
                        
                        if not re.search(r'\d{1,2}[-/ \.]+[A-Za-z0-9]{2,}[-/ \.]+\d{2,4}', date_str):
                            continue
                            
                        try:
                            parsed_date = date_parser.parse(date_str, dayfirst=True)
                        except (ValueError, TypeError, OverflowError):
                            continue
                            
                        details = cleaned_row[col_map['details']] if col_map['details'] != -1 and col_map['details'] < len(cleaned_row) else ''
                        debit_str = cleaned_row[col_map['debit']] if col_map['debit'] != -1 and col_map['debit'] < len(cleaned_row) else ''
                        credit_str = cleaned_row[col_map['credit']] if col_map['credit'] != -1 and col_map['credit'] < len(cleaned_row) else ''
                        balance_str = cleaned_row[col_map['balance']] if col_map['balance'] != -1 and col_map['balance'] < len(cleaned_row) else ''
                        
                        debit = clean_number(debit_str)
                        credit = clean_number(credit_str)
                        balance = clean_number(balance_str)
                        
                        remarks = extract_remarks(details)
                        
                        all_rows.append({
                            'Date': parsed_date.strftime('%Y-%m-%d'),
                            'DETAILS': details,
                            'DEBIT': debit,
                            'CREDIT': credit,
                            'Balance': balance,
                            'Remarks': remarks
                        })

    # Strategy 2: Text Line Extraction Fallback (For borderless/multi-line PDFs like BoB Bank of Baroda)
    if not all_rows:
        with pdfplumber.open(pdf_file, password=password) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ''
                lines = text.split('\n')
                for line in lines:
                    m_date = re.match(r'^\s*(\d{1,2}[-/ \.]+[A-Za-z0-9]{2,}[-/ \.]+\d{2,4})', line)
                    if m_date:
                        date_str = m_date.group(1)
                        try:
                            parsed_date = date_parser.parse(date_str, dayfirst=True)
                        except (ValueError, TypeError, OverflowError):
                            continue
                            
                        rest = line[len(m_date.group(0)):].strip()
                        m_val = re.match(r'^(\d{1,2}[-/ \.]+[A-Za-z0-9]{2,}[-/ \.]+\d{2,4})', rest)
                        if m_val:
                            rest = rest[len(m_val.group(0)):].strip()
                            
                        amounts = re.findall(r'[\d,]+\.\d{2}(?:[CcDd][Rr])?', rest)
                        if len(amounts) >= 2:
                            bal_val = clean_number(amounts[-1])
                            amt_val = clean_number(amounts[-2])
                            
                            amt_pos = rest.rfind(amounts[-2])
                            details = rest[:amt_pos].strip()
                            
                            u_line = line.upper()
                            is_credit = False
                            if 'DEPOSIT' in u_line or 'CR' in amounts[-2].upper() or 'CRED' in u_line:
                                is_credit = True
                                
                            debit = 0.0 if is_credit else amt_val
                            credit = amt_val if is_credit else 0.0
                            
                            all_rows.append({
                                'Date': parsed_date.strftime('%Y-%m-%d'),
                                'DETAILS': details,
                                'DEBIT': debit,
                                'CREDIT': credit,
                                'Balance': bal_val,
                                'Remarks': extract_remarks(details)
                            })

    # Balance-based Credit/Debit Post-Processing Correction
    if len(all_rows) > 0:
        is_reverse = False
        for i in range(1, min(10, len(all_rows))):
            if all_rows[i-1]['Date'] > all_rows[i]['Date']:
                is_reverse = True
                break
            elif all_rows[i-1]['Date'] < all_rows[i]['Date']:
                is_reverse = False
                break
                
        if is_reverse:
            all_rows.reverse()
            
        for i in range(1, len(all_rows)):
            prev_bal = all_rows[i-1]['Balance']
            curr_bal = all_rows[i]['Balance']
            amt = all_rows[i]['DEBIT'] + all_rows[i]['CREDIT']
            
            diff = round(curr_bal - prev_bal, 2)
            if diff > 0 and abs(diff - amt) < 0.01:
                all_rows[i]['CREDIT'] = amt
                all_rows[i]['DEBIT'] = 0.0
            elif diff < 0 and abs(abs(diff) - amt) < 0.01:
                all_rows[i]['DEBIT'] = amt
                all_rows[i]['CREDIT'] = 0.0
                
        if is_reverse:
            all_rows.reverse()
            
    # Re-apply absolute value to balances in case they went negative due to overdraft (Dr) logic
    for row in all_rows:
        row['Balance'] = abs(row['Balance'])
        row['DEBIT'] = abs(row['DEBIT'])
        row['CREDIT'] = abs(row['CREDIT'])

    return pd.DataFrame(all_rows)

st.set_page_config(page_title='Bank Statement to Excel', layout='centered')
st.title('🏦 Bank Statement to Excel Converter')
st.write('Upload your Bank Statement (SBI, BoB, Kotak, etc.) to extract transactions and generate an Excel report.')

uploaded_file = st.file_uploader('Upload PDF Statement', type='pdf')

if uploaded_file is not None:
    needs_password = False
    is_authenticated = False
    pdf_password = ''
    
    try:
        with pdfplumber.open(uploaded_file) as pdf:
            is_authenticated = True
    except PDFPasswordIncorrect:
        needs_password = True
        
    if needs_password:
        st.warning("🔒 This PDF is password protected.")
        pdf_password = st.text_input("Enter PDF Password:", type="password")
        if pdf_password:
            try:
                with pdfplumber.open(uploaded_file, password=pdf_password) as pdf:
                    is_authenticated = True
            except PDFPasswordIncorrect:
                st.error("Incorrect password. Please try again.")
                
    if is_authenticated:
        with st.spinner('Extracting data from PDF...'):
            try:
                df = parse_pdf(uploaded_file, password=pdf_password)
                
                if df.empty:
                    st.error('No transactions found. Please ensure it is a valid bank statement PDF.')
                else:
                    st.success(f'Successfully extracted {len(df)} transactions!')
                    
                    st.subheader('Preview of Transactions')
                    st.dataframe(df.head())
                    
                    # Create Summary Pivot Table
                    summary_df = df.groupby('Remarks')[['CREDIT', 'DEBIT']].sum().reset_index()
                    summary_df.columns = ['Row Labels', 'Sum of CREDIT', 'Sum of DEBIT']
                    
                    # Excel Generation
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='openpyxl') as writer:
                        summary_df.to_excel(writer, sheet_name='Summary', index=False)
                        transactions_df = df[(df['CREDIT'] > 0) | (df['DEBIT'] > 0)]
                        transactions_df.to_excel(writer, sheet_name='Transactions', index=False)
                    output.seek(0)
                    
                    st.download_button(
                        label='📥 Download Excel File',
                        data=output,
                        file_name='bank_statement_extracted.xlsx',
                        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                    )
            except Exception as e:
                st.error(f'An error occurred: {e}')
