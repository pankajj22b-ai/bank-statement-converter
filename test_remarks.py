import app

details = "UPI/DR/545963380399/SWIGGY/UTIB/**WIGGY@AXISB/PAYMENT//ACD3096539492XERBJKJ/03/04/2025 22:14:25"
print("1:", app.extract_remarks(details))

details2 = "UPI/CR/885157135794/SUNIL KAI/HDFC/**417-2@YBL/PAYMENT//YBL6288E386CB194A12B5039EA00EF3BBBD/05/04/2025 12:09:48"
print("2:", app.extract_remarks(details2))

details3 = "PMSBY RENEWAL(25-26)-257564279-06204193"
print("3:", app.extract_remarks(details3))

details4 = "SBINT FOR THE PERIOD FROM28-MAR-25 TO 27-JUN-25"
print("4:", app.extract_remarks(details4))

details5 = "MB-IMPS-DR/VINESHGOHIL/HDFC/**0983/ /26/08/2025 12:03:23/523812834002"
print("5:", app.extract_remarks(details5))
