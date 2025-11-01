# PersonaBot 🤖✨

An intelligent, scalable framework for automating online form submissions using Large Language Models (LLMs) to generate human-like, realistic responses. PersonaBot creates virtual personas and answers survey questions from their unique perspectives.

## 🚀 Key Features

-   **Human-like Answer Generation:** Leverages powerful LLMs (e.g., Google Gemini) to produce responses that are consistent with a specific character's background and story.
-   **Dynamic Persona Creation:** Automatically generates detailed and comprehensive personas based on the context and questions of the target form, ensuring authentic and logical answers.
-   **Extensible Provider Architecture:** Built on the Strategy Design Pattern, allowing for easy expansion to support various form platforms (e.g., Google Forms, Porsline, SurveyMonkey) with minimal code changes.
-   **Intelligent API Key Management:** Utilizes a thread-safe, round-robin `APIKeyManager` to rotate through multiple API keys, effectively avoiding rate limits and enhancing scalability.
-   **Robust Dynamic Form Handling:** Employs Playwright's smart-wait capabilities to reliably interact with modern, JavaScript-heavy forms, minimizing the fragility of the automation.
-   **Decoupled Multi-Phase Process:** The entire workflow is broken down into four distinct, logical phases: Schema Extraction, Persona Generation, Answer Generation, and Form Submission.

## 🏛️ Architecture & Workflow

The project is designed with a modular and decoupled architecture, separating the core business logic from platform-specific implementations (Providers).

1.  **Core (`/core`):** Contains the platform-agnostic business logic:
    -   `persona_generator.py`: Creates personas based on the form schema.
    -   `answer_generator.py`: Generates answers for each persona.
    -   `services.py`: Provides shared services like the `APIKeyManager`.

2.  **Providers (`/providers`):** Each subdirectory supports a specific form platform and implements two key responsibilities:
    -   `schema_extractor.py`: Extracts the form's structure (questions, options, IDs).
    -   `form_submitter.py`: Submits the generated answers to the live form.

### Workflow

The process runs through four sequential phases:
1.  **Phase 1 (Schema Extraction):** The structure of the target online form is extracted and saved to `output/form_schema.json`.
2.  **Phase 2 (Persona Generation):** Using the schema, a specified number of personas are generated and saved as individual JSON files in `output/personas/`.
3.  **Phase 3 (Answer Generation):** For each persona, a corresponding set of answers is generated and saved in `output/answers/`.
4.  **Phase 4 (Form Submission):** The generated answer files are read and submitted to the online form one by one.

## 📁 Project Structure

```
.
├── .env                          # Environment variables configuration file
├── config.py                     # Central configuration loader and manager
├── main.py                       # Main orchestrator to run the workflow phases
├── requirements.txt              # Python dependency list
├── utils.py                      # General utility functions
├── prompts/                      # Directory for prompt engineering JSON files
│   ├── persona_generation_prompt.json
│   └── answer_generation_prompt.json
├── core/                         # Core platform-agnostic logic
│   ├── persona_generator.py
│   ├── answer_generator.py
│   └── services.py
└── providers/                    # Modules for specific form platforms
    └── google_forms/
        ├── schema_extractor.py
        └── form_submitter.py
```

## 🔧 Getting Started

Follow these steps to set up and run the project locally.

**1. Clone the Repository:**
```bash
git clone https://github.com/your-username/PersonaBot.git
cd PersonaBot
```

**2. Create and Activate a Virtual Environment:**
```bash
# For Linux/macOS
python3 -m venv venv
source venv/bin/activate

# For Windows
python -m venv venv
.\venv\Scripts\activate
```

**3. Install Dependencies:**
```bash
pip install -r requirements.txt
```

**4. Install Playwright Browsers:**
(This command downloads the necessary browser binaries for automation)
```bash
playwright install
```

## ⚙️ Configuration

Before running the bot, you must configure your environment variables.

1.  Create a file named `.env` in the project's root directory.
2.  Copy the contents below into the file and replace the placeholder values with your own.

```ini
# .env.example

# --- Google Gemini API Keys ---
# Add your keys following this pattern. You can have as many as you need.
GOOGLE_API_KEY_1="YOUR_GEMINI_API_KEY_HERE_1"
GOOGLE_API_KEY_2="YOUR_GEMINI_API_KEY_HERE_2"
# GOOGLE_API_KEY_3="..."

# --- AI Model Settings ---
# The name of the model you want to use
GEMINI_MODEL_NAME="gemini-1.5-flash"

# --- Form & Automation Settings ---
# The full URL of the online form to be filled
BASE_FORM_URL="https://docs.google.com/forms/d/e/YOUR_FORM_ID/viewform"

# --- Playwright Settings ---
# Set to "False" to watch the bot in action
HEADLESS_MODE="True"
# Slows down Playwright actions (in milliseconds). Useful for debugging.
SLOW_MO="50"
```

## 🏃 How to Run

The main entry point is `main.py`, which orchestrates the phases based on command-line arguments.

**General Command Format:**
```bash
python main.py [provider_name] [--phases PHASES] [--num-personas N]
```

-   `provider_name`: The name of the provider to use (must match a directory in `providers/`). **(Required)**
-   `--phases`: A comma-separated list of phases to run (e.g., `1,2,3,4`). Default is all phases.
-   `--num-personas`: The number of personas to generate in Phase 2. Default is `5`.

### Usage Examples:

**1. Run the complete workflow for Google Forms:**
```bash
python main.py google_forms
```

**2. Run only the schema extraction (Phase 1):**
```bash
python main.py google_forms --phases 1
```

**3. Extract schema and generate 10 personas (Phase 1 & 2):**
```bash
python main.py google_forms --phases 1,2 --num-personas 10
```

**4. Run only the form submission (Phase 4):**
(This assumes you have already generated answer files in `output/answers/`)
```bash
python main.py google_forms --phases 4
```

## 🌱 How to Extend (Adding a New Provider)

The provider architecture makes it easy to add support for new form platforms.

1.  Create a new directory inside `providers/` (e.g., `providers/porsline/`).
2.  Inside your new directory, create two files:
    -   `schema_extractor.py`: This file must contain an async function `run()` that handles extracting the form schema and saving it to `form_schema.json`.
    -   `form_submitter.py`: This file must contain an async function `run()` that reads answer files and submits them to the target form.
3.  You can now run the bot with your new provider name: `python main.py porsline`.

## 📝 License

This project is licensed under the MIT License. See the `LICENSE` file for more details.