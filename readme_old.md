# **MonWatch-UI Prototype**



### **This is a prototype!**



MonWatch-UI is an experimental UI tool for browsing and previewing Himawari satellite data. Built overnight as a proof of concept, it provides a simple interface to navigate AWS-hosted NOAA Himawari files by date, time, and spectral band.



**Features**

* Automatic Python installation via run.bat (requires administrator privileges).
* Interactive user interface for file navigation and preview.
* Drag-and-drop support for loading satellite data files.
* Manual folder navigation for granular control (date → time → band).



**Requirements**

* Windows OS (tested on Windows 10/11).
* Administrator privileges to run run.bat.
* Internet connection (for initial AWS data access).



**Installation**

1. Clone or download the repository to your local machine.
2. Right-click run.bat and select Run as administrator. 
   (This will install Python and required dependencies automatically.)
3. Once installation completes, the MonWatch-UI UI will launch.



**Usage**

1. Drag-and-Drop: Simply drag AWS Himawari files into the main list panel to load and preview them.
2. Manual Folder Navigation:

* Click Open Folder in the UI.
* Navigate into a date folder to view available time stamps.
* Within each time folder, select a spectral band for preview.



**Data Source and Limitations**

* Data is sourced from AWS buckets hosting NOAA Himawari imagery.
* Limitation: These files are not in the official "Himawari Cast" format, so latitude/longitude metadata is unavailable.



**Known Issues**

* UI glitches and occasional crashes (prototype code was developed overnight).
* No georeferencing support due to missing coordinate metadata.
* Performance may vary depending on file size and system specs.

The detailed roadmap and feature plans for upcoming releases are outlined in the monwatch-ui Structure.txt file under the monwatch-ui folder. Please refer to that document for full details on enhancements, UI revisions, performance optimizations, and new data processing workflows.



**Future Improvements**

* Add automatic georeferencing using ancillary metadata or external lookup tables.
* Improve UI stability and error handling.
* Implement batch processing and metadata extraction.



Contributing



Contributions are welcome! Please open an issue or submit a pull request for any bugs, feature requests, or improvements.



License



This prototype is released under the PWARDS License. See LICENSE for details.



Enjoy exploring Himawari data with MonWatch-UI!

