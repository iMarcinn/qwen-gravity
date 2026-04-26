# qwen-gravity
Qwen Gravity is an agentic coding assistant with a web interface. It allows you to chat with local LLMs (via Ollama) and give them access to a local workspace for coding tasks.

<img width="1920" height="1080" alt="Screenshot (118)" src="https://github.com/user-attachments/assets/c412b56a-b911-43a2-941e-d47ddc3b2c81" />

<img width="524" height="480" alt="Screenshot 2026-04-26 120736" src="https://github.com/user-attachments/assets/fb4356b6-75f2-4180-a1e6-ab7432390e41" />



## Features

- **Local LLM Support**: Works with any model available in Ollama (Qwen 2.5 Coder, Llama 3, etc.).
  
- **Dynamic Model Switching**: Change models on-the-fly via the Settings menu.
  
- **Smart File Access**: Upload PDFs, DOCX, XLSX, and SQLite databases — the agent automatically extracts text for analysis.
  
- **Workspace Integration**: The agent can read and write files in a dedicated local workspace.
  
- **Rich UI**: Modern dark-themed interface with markdown support, code highlighting, and tool execution tracking.
  

## Prerequisites

1.  **Ollama**: Install [Ollama](https://ollama.com/) and pull a model.
   
2.  **Python 3.10+**: Ensure Python is installed on your system.

## Installation

1.  Clone this repository: git clone https://github.com/iMarcinn/qwen-gravity.git
   
    cd qwen-gravity
    
2.  Install dependencies: pip install -r requirements.txt
  
   
## Running the App

1. Start Ollama (MUST)

2.  **Open a Terminal**: Navigate to the folder where you downloaded Qwen Gravity.
   
    *   *Windows*: Right-click the folder and select "Open in Terminal" or "Open PowerShell window here".
      
    *   *Mac/Linux*: Open your terminal and use `cd` to enter the directory.
      
3.  **Start the Server**: Run the following command:
   `
    python app.py
    `
    
    
4.  **Access the UI**: Once the terminal shows that the server is running, open your web browser and go to:
    `http://localhost:5000`

The application will automatically attempt to connect to your local Ollama instance and load the workspace.    
