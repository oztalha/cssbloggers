# This file is called when the app spins up. 
# 
# I don't use this for anything, except to add the lib subdirectory 
# to the sys.path, so I can load the third-party libraries.
import sys
import os.path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib'))
