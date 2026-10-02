
=====PAGE 1=====
10 DECEMBER 2024 
LNIS – Applicable Document 1 – Volume A – Annex 3 – PRN Spreading Codes README 
Note: this document is referenced to as [Annex3] within LNIS Applicable Document 1 – Volume A. 
Hexadecimal Codes 
The complete set of LunaNet AFS spreading codes for PRNs 1-210 are provided electronically in HEX 
(hexadecimal format) for development purposes. This allows the implementation of the spreading codes as 
memory codes. Alternatively, the codes can be generated in real time using the indexed code approach 
described in LNIS AD1 Volume A. 
The names of the files containing the codes are listed in Table 1, and provided electronically as attachments 
to LNIS AD1 Volume A. 
Table 1. List of Spreading Code Electronic Files 
Signal 
Code 
Code Length 
[symbols] 
Filename 
AFS-I 
Primary Code 
2046 
#1_GoldCode2046hex210prns.txt 
AFS-Q 
Primary Code 
10230 
#2_l1cp_hex210prns.txt 
Tertiary Code 
1500 
#3_Weil1500hex210prns.txt 
 
Note that codes with length of 2046 and 10230 symbols are not divisible by 4.  In order to represent these 
codes in hexadecimal format and provide them electronically, two zeros are added to the MSB (i.e., to the 
lefthand side) for all PRNs 1-210. To recover the 2046-chip code for the data channel in binary format, the 
hexadecimal format can be converted to 2048 binary bits and the two MSBs (leftmost bits) removed. To 
recover the 10230-chip Q channel code, the hexadecimal format can be converted to 10232 binary bits and 
the two MSBs removed. 
Binary Codes 
For completeness, the AFS-Q secondary codes are provided in binary format in Table 2, as well as in LNIS 
AD1 Volume A. 
Table 2. AFS-Q Secondary Codes 
Secondary Code Identifier 
Secondary Code (binary) 
S0 
1110  
S1 
0111 
S2 
1011 
S3 
1101 
 
Each PRN from Table 2 is assigned to each secondary code 𝑆𝑆𝑘𝑘 with k > 3 according with (Eq. 1) below for 
initial development. 
 
𝑘𝑘= ൜𝑖𝑖−1                 for 𝑖𝑖≤4
mod(𝑖𝑖−1,4) for 𝑖𝑖> 4 
(1) 
 
