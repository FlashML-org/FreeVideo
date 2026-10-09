"""PyInstaller entry point for the Linux AppImage; model modules stay out of the launcher."""
from freevideo_engine.modern_launcher import main

if __name__ == '__main__':
    main()
