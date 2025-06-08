#!/usr/bin/env python3
"""
My Poker Face - Setup Helper
Helps users quickly set up their environment for playing
"""

import os
import sys
import platform
import subprocess
from pathlib import Path

def print_banner():
    print("\n" + "="*50)
    print("🃏 MY POKER FACE - SETUP HELPER 🃏")
    print("="*50 + "\n")

def check_python_version():
    """Check if Python version is 3.8 or higher"""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 8):
        print("❌ Python 3.8 or higher is required")
        print(f"   You have Python {version.major}.{version.minor}.{version.micro}")
        return False
    print(f"✅ Python {version.major}.{version.minor}.{version.micro} detected")
    return True

def create_venv():
    """Create virtual environment if it doesn't exist"""
    venv_name = "my_poker_face_venv"
    if os.path.exists(venv_name):
        print(f"✅ Virtual environment '{venv_name}' already exists")
        return venv_name
    
    print(f"📦 Creating virtual environment '{venv_name}'...")
    try:
        subprocess.run([sys.executable, "-m", "venv", venv_name], check=True)
        print("✅ Virtual environment created")
        return venv_name
    except subprocess.CalledProcessError:
        print("❌ Failed to create virtual environment")
        print("   Try running: python -m pip install --user virtualenv")
        return None

def get_activation_command(venv_name):
    """Get the correct activation command for the OS"""
    system = platform.system()
    if system == "Windows":
        return f"{venv_name}\\Scripts\\activate.bat"
    else:
        return f"source {venv_name}/bin/activate"

def create_env_file():
    """Create .env file if it doesn't exist"""
    if os.path.exists(".env"):
        print("✅ .env file already exists")
        return
    
    print("\n🔑 OpenAI API Key Setup")
    print("-" * 30)
    print("To use AI personalities, you need an OpenAI API key.")
    print("Get one at: https://platform.openai.com/api-keys")
    print("\nYou can skip this for now and play with mock AI.")
    
    api_key = input("\nEnter your OpenAI API key (or press Enter to skip): ").strip()
    
    with open(".env", "w") as f:
        if api_key:
            f.write(f"OPENAI_API_KEY={api_key}\n")
            print("✅ .env file created with API key")
        else:
            f.write("# OPENAI_API_KEY=your-key-here\n")
            print("✅ .env file created (no API key - using mock AI)")

def install_requirements(venv_name):
    """Install requirements in the virtual environment"""
    system = platform.system()
    if system == "Windows":
        pip_path = f"{venv_name}\\Scripts\\pip"
        python_path = f"{venv_name}\\Scripts\\python"
    else:
        pip_path = f"{venv_name}/bin/pip"
        python_path = f"{venv_name}/bin/python"
    
    print("\n📚 Installing requirements...")
    print("This may take a few minutes on first run...")
    
    try:
        # First upgrade pip
        subprocess.run([python_path, "-m", "pip", "install", "--upgrade", "pip"], 
                      capture_output=True)
        
        # Then install requirements
        result = subprocess.run([pip_path, "install", "-r", "requirements.txt"], 
                              capture_output=True, text=True)
        
        if result.returncode == 0:
            print("✅ All requirements installed successfully")
            return True
        else:
            print("❌ Some requirements failed to install")
            print("Error:", result.stderr)
            return False
    except Exception as e:
        print(f"❌ Failed to install requirements: {e}")
        return False

def print_next_steps(venv_name):
    """Print instructions for running the game"""
    activation_cmd = get_activation_command(venv_name)
    
    print("\n" + "="*50)
    print("🎉 SETUP COMPLETE!")
    print("="*50)
    
    print("\n📋 Next Steps:")
    print("-" * 30)
    
    print(f"\n1. Activate the virtual environment:")
    print(f"   {activation_cmd}")
    
    print("\n2. Run the game:")
    print("   python working_game.py")
    
    print("\n💡 Tips:")
    print("- First time? Start with 'Quick Game' option")
    print("- No API key? You'll play against mock AI (still fun!)")
    print("- See QUICK_START.md for more help")
    
    print("\n" + "="*50)
    print("Happy playing! May the best hand win! 🏆")
    print("="*50 + "\n")

def main():
    """Main setup process"""
    print_banner()
    
    # Check Python version
    if not check_python_version():
        sys.exit(1)
    
    # Create virtual environment
    venv_name = create_venv()
    if not venv_name:
        sys.exit(1)
    
    # Install requirements
    if not install_requirements(venv_name):
        print("\n⚠️  Setup completed with warnings")
        print("You may need to install some packages manually")
    
    # Create .env file
    create_env_file()
    
    # Print next steps
    print_next_steps(venv_name)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Setup cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Setup failed: {e}")
        sys.exit(1)