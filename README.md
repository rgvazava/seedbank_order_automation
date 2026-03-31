\# Seedbank Order Automation System



This project automates the processing of international germplasm orders. It takes a raw export file and turns it into organized outputs that can actually be used for day-to-day work.



\-------------------



\## What it does



Given a CSV or Excel export, the system will:



\- filter out domestic orders and keep only international ones

\- generate APHIS PDF paperwork for each order

\- group orders into curator-specific folders

\- create structured text summaries for each order

\- generate ready-to-use email packets for manual sending



The goal was to remove repetitive copy-paste work and make the process more consistent.



\--------------------





## How to run



1\. Put your CSV or Excel file in the `inputs/` folder  

&#x20;  (or just drag and drop it onto `run\_orders.bat`)



2\. Run: run\_orders.bat



3\. Check the `outputs/` folder for results




\## Example input

A sample file is included here: inputs\\SQL\_GRIN\_ORDERS

This shows the expected format and columns.




## Tech used


\- Python (pandas, pypdf)

\- Windows batch scripting

\- basic file system automation




## Notes


\- This is a simplified/public version of a larger internal workflow tool  

\- All data and email addresses in this repo are placeholders  

\- You can adjust the config or templates depending on your use case  





## Why I built this

The original process involved a lot of manual steps, copying data, organizing files, and drafting emails. This project was built to streamline that into a single, repeatable workflow.

It’s designed so that someone non-technical can run it without needing to touch the code.







