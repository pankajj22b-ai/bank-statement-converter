import streamlit as st
import pandas as pd
import pdfplumber
import io
import re
from dateutil import parser as date_parser

def extract_remarks(details):
    details_upper = details.upper()
    if 'ATM WDL' in details_upper or 'ATM CASH' in details_upper: return 'Atm cash withdrawal'
    if 'LIC OF' in details_upper: return 'LIC'
    if 'GST' in details_upper: return 'GST Paid'
    if 'GROWW' in details_upper: return 'Groww'
    if 'DREAMPLUG' in details_upper: return 'Dreamplug'
    if 'CBDT' in details_upper or 'INCOME TAX' in details_upper: return 'CBDT'
    if 'INTEREST CREDIT' in details_upper: return 'INTEREST'
    if 'CHEQUE' in details_upper:
        if 'CLEARING' in details_upper: return 'Cheque Deposited'
        if 'WITHDRAWAL' in details_upper: return 'Cash Withdrawals'
        return 'Cheque Paid'
    
    # Custom names heuristic
    names = ['AMBIKA', 'TULSI', 'JYOTSANA', 'SILVER FAB', 'KIRAN', 'MAJISHA', 'SWASTIK', 'FIN INDIA', 'INDIAN CLE']
    for name in names:
        if name in details_upper:
            if name == 'INDIAN CLE': return 'Fin India'
            return name.title()

    if 'NEFT' in details_upper: return 'NEFT'
    if 'IMPS' in details_upper: return 'IMPS'
    if 'RTGS' in details_upper: return 'RTGS'
    if 'UPI' in details_upper: return 'UPI'
    if 'WDL TFR' in details_upper or 'WITHDRAWAL' in details_upper: return 'Withdrawals'
    
    return 'Other'

def clean_number(val_str):
    if not val_str:
        return 0.0
    s = str(val_str).replace(',', '').strip().upper()
    if s == '-' or s == '':
        return 0.0
    s = s.replace('CR', '').replace('DR', '').replace('PR', '').replace(' INR', '').replace('₹', '')
    try:
        return float(s)
    except ValueError:
        return 0.0

def parse_pdf(pdf_file):
    all_rows = []
    
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
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
                            
                        # If header not found, infer from the first valid data row
                        for i, cell in enumerate(cleaned_row):
                            if re.search(r'\d{1,2}[-/ \.]+[A-Za-z0-9]{2,}[-/ \.]+\d{2,4}', cell):
                                date_idx = i
                                break
                                
                        if date_idx != -1 and len(cleaned_row) >= 5:
                            col_map['date'] = date_idx
                            col_map['balance'] = len(cleaned_row) - 1
                            col_map['credit'] = len(cleaned_row) - 2
                            col_map['debit'] = len(cleaned_row) - 3
                            
                            max_len = -1
                            det_idx = -1
                            for i in range(date_idx + 1, col_map['debit']):
                                if len(cleaned_row[i]) > max_len:
                                    max_len = len(cleaned_row[i])
                                    det_idx = i
                            col_map['details'] = det_idx if det_idx != -1 else date_idx + 1
                            inferred_col_map = True
                            # Continue down to process this row since it's valid data
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

    return pd.DataFrame(all_rows)

st.set_page_config(page_title='Bank Statement to Excel', layout='centered')
st.title('🏦 Bank Statement to Excel Converter')
st.write('Upload your Bank Statement (SBI, BoB, Kotak, etc.) to extract transactions and generate an Excel report.')

uploaded_file = st.file_uploader('Upload PDF Statement', type='pdf')

if uploaded_file is not None:
    with st.spinner('Extracting data from PDF...'):
        try:
            df = parse_pdf(uploaded_file)
            
            if df.empty:
                st.error('No transactions found. Please ensure it is a valid bank statement with a tabular layout.')
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
                    transactions_df = df[df['CREDIT'] > 0]
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
