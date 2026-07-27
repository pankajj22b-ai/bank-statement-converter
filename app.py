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
        return 'UPI'

    # Specific Canara Remarks
    if 'PMSBY RENEWAL' in u: return 'PMSBY RENEWAL'
    if 'SBINT' in u: return 'SBINT'

    # 4. Fallback for NEFT / IMPS / RTGS / IFT / IPO
    if 'NEFT' in u: return 'NEFT'
    if 'IMPS' in u: return 'IMPS'
    if 'RTGS' in u: return 'RTGS'
    if 'IFT/' in u: return 'IFT'
    if 'IPO/' in u: return 'IPO'
    if 'TCFSL' in u: return 'TCFSL'
    if 'WDL TFR' in u or 'WITHDRAWAL' in u: return 'Withdrawals'

    return 'Other'

def clean_number(val_str, is_balance=False):
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
            return -val if (is_dr and is_balance) else val
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
                
                in_summary_block = False
                # Process dynamically without relying on headers
                for row in table:
                    cleaned_row = [str(cell).replace('\n', ' ').strip() if cell else '' for cell in row]
                    row_text = ' '.join(cleaned_row).upper()
                    
                    # Skip summary tables/blocks
                    if 'TOTAL DEBIT' in row_text and 'TOTAL CREDIT' in row_text:
                        in_summary_block = True
                        continue
                        
                    if in_summary_block:
                        has_text = any(re.search(r'[A-Za-z]', c) for c in cleaned_row)
                        if not has_text:
                            continue
                        else:
                            in_summary_block = False
                            
                    # Skip informational balance rows without a date
                    if any(k in row_text for k in ['OPENING BALANCE', 'CLOSING BALANCE', 'BROUGHT FORWARD', 'B/F', 'CARRIED FORWARD', 'C/F']):
                        date_found = False
                        for c in cleaned_row:
                            if re.search(r'\d{1,2}[-/ \.]+[A-Za-z0-9]{2,}[-/ \.]+\d{2,4}', c):
                                date_found = True
                                break
                        if not date_found:
                            continue
                    
                    # 1. Find Date
                    date_idx = -1
                    for i, cell in enumerate(cleaned_row):
                        if re.search(r'\d{1,2}[-/ \.]+[A-Za-z0-9]{2,}[-/ \.]+\d{2,4}', cell):
                            date_idx = i
                            break
                            
                    # 2. Find Amounts
                    amt_cols = []
                    for i in range(len(cleaned_row)):
                        if i == date_idx: continue
                        cell_clean = cleaned_row[i].replace(' ', '').replace(',', '')
                        if re.match(r'^-?\d+\.\d{2}(?:\(?[CcDd][Rr]\)?)?$', cell_clean):
                            amt_cols.append(i)
                            
                    if date_idx != -1:
                        # NEW TRANSACTION ROW
                        date_str = cleaned_row[date_idx]
                        try:
                            parsed_date = date_parser.parse(date_str, dayfirst=True)
                        except (ValueError, TypeError, OverflowError):
                            continue
                            
                        # Find details
                        details = ''
                        if len(amt_cols) > 0:
                            det_parts = [cleaned_row[i] for i in range(date_idx + 1, amt_cols[0]) if cleaned_row[i]]
                            details = ' '.join(det_parts)
                        else:
                            det_parts = [cleaned_row[i] for i in range(date_idx + 1, len(cleaned_row)) if cleaned_row[i]]
                            details = ' '.join(det_parts)
                            
                        # Extract amounts
                        balance = 0.0
                        amt = 0.0
                        if len(amt_cols) >= 3:
                            balance = clean_number(cleaned_row[amt_cols[-1]], is_balance=True)
                            amt = max(clean_number(cleaned_row[amt_cols[-2]], is_balance=False), clean_number(cleaned_row[amt_cols[-3]], is_balance=False))
                        elif len(amt_cols) == 2:
                            balance = clean_number(cleaned_row[amt_cols[-1]], is_balance=True)
                            amt = clean_number(cleaned_row[amt_cols[-2]], is_balance=False)
                        elif len(amt_cols) == 1:
                            amt = clean_number(cleaned_row[amt_cols[0]], is_balance=False)
                            
                        all_rows.append({
                            'Date': parsed_date.strftime('%Y-%m-%d'),
                            'DETAILS': details,
                            'DEBIT': amt, # Initially map to Debit, Post-Processor fixes it
                            'CREDIT': 0.0,
                            'Balance': balance,
                            'Remarks': extract_remarks(details)
                        })
                    else:
                        # CONTINUATION ROW
                        if not all_rows: continue
                        
                        det_parts = [cleaned_row[i] for i in range(len(cleaned_row)) if i not in amt_cols and cleaned_row[i]]
                        details = ' '.join(det_parts)
                        
                        if details:
                            all_rows[-1]['DETAILS'] += ' ' + details
                            all_rows[-1]['Remarks'] = extract_remarks(all_rows[-1]['DETAILS'])
                            
                        if len(amt_cols) >= 3:
                            balance = clean_number(cleaned_row[amt_cols[-1]], is_balance=True)
                            amt = max(clean_number(cleaned_row[amt_cols[-2]], is_balance=False), clean_number(cleaned_row[amt_cols[-3]], is_balance=False))
                            if amt > 0: all_rows[-1]['DEBIT'] = amt
                            if balance > 0: all_rows[-1]['Balance'] = balance
                        elif len(amt_cols) == 2:
                            balance = clean_number(cleaned_row[amt_cols[-1]], is_balance=True)
                            amt = clean_number(cleaned_row[amt_cols[-2]], is_balance=False)
                            if amt > 0: all_rows[-1]['DEBIT'] = amt
                            if balance > 0: all_rows[-1]['Balance'] = balance
                        elif len(amt_cols) == 1:
                            amt = clean_number(cleaned_row[amt_cols[0]], is_balance=False)
                            if amt > 0: all_rows[-1]['DEBIT'] = amt

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
                            
                        amounts = re.findall(r'[\d,]+\.\d{2}(?:\(?[CcDd][Rr]\)?)?', rest)
                        if len(amounts) >= 2:
                            bal_val = clean_number(amounts[-1], is_balance=True)
                            amt_val = clean_number(amounts[-2], is_balance=False)
                            
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

    # Strategy 3: OCR Fallback for Scanned Image PDFs (like HDFC)
    if not all_rows:
        try:
            import pytesseract
            from pdf2image import convert_from_path, convert_from_bytes
            
            if isinstance(pdf_file, str):
                images = convert_from_path(pdf_file)
            else:
                pdf_file.seek(0)
                images = convert_from_bytes(pdf_file.read())
            
            for img in images:
                text = pytesseract.image_to_string(img, config='--psm 6')
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
                            
                        amounts = re.findall(r'[\d,]+\.\d{2}(?:\(?[CcDd][Rr]\)?)?', rest)
                        if len(amounts) >= 2:
                            bal_val = clean_number(amounts[-1], is_balance=True)
                            amt_val = clean_number(amounts[-2], is_balance=False)
                            
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
        except ImportError:
            pass # pytesseract or pdf2image not installed
        except Exception as e:
            pass # Handle other errors silently to allow Streamlit to show empty df message


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
            
        for i in range(len(all_rows)):
            curr_bal = all_rows[i]['Balance']
            amt = all_rows[i]['DEBIT'] + all_rows[i]['CREDIT']
            
            if i > 0:
                prev_bal = all_rows[i-1]['Balance']
                diff = round(curr_bal - prev_bal, 2)
                
                if diff > 0:
                    all_rows[i]['CREDIT'] = amt
                    all_rows[i]['DEBIT'] = 0.0
                elif diff < 0:
                    all_rows[i]['DEBIT'] = amt
                    all_rows[i]['CREDIT'] = 0.0
                else:
                    # If diff == 0 or previous row missing, fallback to heuristics
                    u_det = all_rows[i]['DETAILS'].upper()
                    if '/CR/' in u_det or ' CR ' in u_det or 'DEPOSIT' in u_det:
                        all_rows[i]['CREDIT'] = amt
                        all_rows[i]['DEBIT'] = 0.0
                    elif '/DR/' in u_det or ' DR ' in u_det or 'WITHDRAWAL' in u_det:
                        all_rows[i]['DEBIT'] = amt
                        all_rows[i]['CREDIT'] = 0.0
            else:
                # For Row 0, use heuristics
                u_det = all_rows[i]['DETAILS'].upper()
                if '/CR/' in u_det or ' CR ' in u_det or 'DEPOSIT' in u_det:
                    all_rows[i]['CREDIT'] = amt
                    all_rows[i]['DEBIT'] = 0.0
                elif '/DR/' in u_det or ' DR ' in u_det or 'WITHDRAWAL' in u_det:
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

# Hide Streamlit toolbar, hamburger menu, GitHub link, Fork button, and footer
hide_streamlit_style = """
<style>
#MainMenu {visibility: hidden;}
header {visibility: hidden;}
footer {visibility: hidden;}
.stApp [data-testid="stToolbar"] {display: none;}
.stApp [data-testid="stDecoration"] {display: none;}
</style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

st.title('Bank Statement to Excel Converter')
st.write('Upload your Bank Statement (SBI, BoB, Kotak, etc.), to extract transactions and generate an Excel report.')

uploaded_file = st.file_uploader('Upload PDF Statement', type='pdf')

if uploaded_file is not None:
    needs_password = False
    is_authenticated = False
    pdf_password = ''
    
    try:
        with pdfplumber.open(uploaded_file) as pdf:
            is_authenticated = True
    except Exception as e:
        if type(e).__name__ in ['PDFPasswordIncorrect', 'PdfminerException']:
            needs_password = True
        else:
            st.error(f"Failed to open PDF: {e}")
        
    if needs_password:
        st.warning("🔒 This PDF is password protected.")
        pdf_password = st.text_input("Enter PDF Password:", type="password")
        if pdf_password:
            try:
                with pdfplumber.open(uploaded_file, password=pdf_password) as pdf:
                    is_authenticated = True
            except Exception as e:
                if type(e).__name__ in ['PDFPasswordIncorrect', 'PdfminerException']:
                    st.error("Incorrect password. Please try again.")
                else:
                    st.error(f"Failed to open PDF: {e}")
                
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
