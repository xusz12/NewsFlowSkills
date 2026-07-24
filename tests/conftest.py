import sys


# Keep plain pytest runs from writing bytecode into the install payload.
sys.dont_write_bytecode = True
