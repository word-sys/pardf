#!/usr/bin/env python3
import sys
import os

# Add the parent directory of 'pardus_pdf_editor' package to the Python path
# This allows running the script directly from the project root.
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

# Import the main entry point from the package
from pardus_pdf_editor.main import main

if __name__ == '__main__':
    sys.exit(main())