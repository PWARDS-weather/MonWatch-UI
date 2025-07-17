# MonWatch-UI
MonWatch‑UI - A Windows prototype UI for browsing and previewing NOAA Himawari satellite imagery hosted on AWS. Features drag‑and‑drop data loading, manual folder navigation by date/time/band, and automatic Python setup via a single run.bat.


<img width="1435" height="934" alt="image" src="https://github.com/user-attachments/assets/56f6f44a-7105-46d7-9f3f-276a29428cd2" />


**QUALITY 0.25**
<img width="1439" height="928" alt="image" src="https://github.com/user-attachments/assets/4e6f1744-c782-448f-9982-142b38788813" />

**QUALITY 0.5**
<img width="1446" height="930" alt="image" src="https://github.com/user-attachments/assets/841e8026-e6bd-4fb3-bfc3-ef9b2cf0db41" />

**QUALITY 1x**
<img width="1437" height="930" alt="image" src="https://github.com/user-attachments/assets/23eb8860-0751-4ed3-841c-d75ff2163dd6" />


<img width="1437" height="930" alt="image" src="https://github.com/user-attachments/assets/23eb8860-0751-4ed3-841c-d75ff2163dd6" />

**VS**

HOMESERVER PROCESSED IMAGE:
<img width="1435" height="941" alt="image" src="https://github.com/user-attachments/assets/5228416d-7b9c-4a87-9162-422218f23875" />
.
.
.

<img width="853" height="773" alt="image" src="https://github.com/user-attachments/assets/080783ec-2a39-4285-a805-17f0b25dbd52" />

**VS**

HOMESERVER PROCESSED IMAGE:
<img width="1451" height="931" alt="image" src="https://github.com/user-attachments/assets/6d73cc51-470c-40dc-a733-bf7d00419de1" />



**MonWatch-UI Prototype**

**This is a prototype!**

MonWatch-UI is an experimental UI tool for browsing and previewing Himawari satellite data. Built overnight as a proof of concept, it provides a simple interface to navigate AWS-hosted NOAA Himawari files by date, time, and spectral band.

**Features**

⦁	Automatic Python installation via run.bat (requires administrator privileges).

⦁	Interactive user interface for file navigation and preview.

⦁	Drag-and-drop support for loading satellite data files.

⦁	Manual folder navigation for granular control (date → time → band).

**Requirements**
⦁	Windows OS (tested on Windows 10/11).

⦁	Administrator privileges to run run.bat.

⦁	Internet connection (for initial AWS data access).

**Installation**
1.	Clone or download the repository to your local machine.
2.	Right-click run.bat and select Run as administrator.
(This will install Python and required dependencies automatically.)
3.	Once installation completes, the MonWatch-UI UI will launch.

**Usage**
1.	Drag-and-Drop: Simply drag AWS Himawari files into the main list panel to load and preview them.
2.	Manual Folder Navigation:
   
⦁	Click Open Folder in the UI.

⦁	Navigate into a date folder to view available time stamps.

⦁	Within each time folder, select a spectral band for preview.

**Data Source and Limitations**

⦁	Data is sourced from AWS buckets hosting NOAA Himawari imagery.

⦁	Limitation: These files are not in the official "Himawari Cast" format, so latitude/longitude metadata is unavailable.

**Known Issues**

⦁	UI glitches and occasional crashes (prototype code was developed overnight).

⦁	No georeferencing support due to missing coordinate metadata.

⦁	Performance may vary depending on file size and system specs.


**Future Improvements**

⦁	Add automatic georeferencing using ancillary metadata or external lookup tables.

⦁	Improve UI stability and error handling.

⦁	Implement batch processing and metadata extraction.

The detailed roadmap and feature plans for upcoming releases are outlined in the monwatch-ui Structure.txt file under the monwatch-ui folder. Please refer to that document for full details on enhancements, UI revisions, performance optimizations, and new data processing workflows.

**Contributing**

Contributions are welcome! Please open an issue or submit a pull request for any bugs, feature requests, or improvements.


**Enjoy exploring Himawari data with MonWatch-UI!**
