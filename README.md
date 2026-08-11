# loadout-manager-
Setup guide (plain text)

What you need before running this:

Python 3.9+ — download from python.org/downloads. On install, check "Add Python to PATH."
Two Python packages — open a terminal/command prompt and run:
   pip install --user pillow pytesseract
Tesseract-OCR (the actual OCR engine — pytesseract is just a wrapper around it) — download and install from github.com/UB-Mannheim/tesseract/wiki. During install, let it add itself to PATH. If it doesn't, open the script and set TESSERACT_PATH near the top to wherever tesseract.exe got installed.
Run it — double-click the .py file, or run python loadout_manager.py from a terminal in the same folder.

How it works day to day:

Click "Add Storage Box," name it, then drag once around one item slot and once around the whole grid — it figures out rows/columns/spacing itself.
Right-click any slot to upload your own reference image for that item.
Hit "Calibrate Count" once per box so it knows where the stack-number sits on screen.
Hit "Capture Now" any time: slots you gave an image get that image + the live OCR'd count; every other slot just shows whatever the screenshot grabbed for it, with its count too.
