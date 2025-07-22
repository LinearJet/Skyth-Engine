# SKYTH Engine

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/downloads/)


**SKYTH** is a sophisticated, agentic AI web application engineered for real-time research, dynamic multimodal interaction, and deep content analysis. It leverages a powerful, modular pipeline architecture driven by LLM-based routing to intelligently handle a wide array of user queries. From generating interactive data visualizations and detailed research reports to analyzing images and synthesizing voice, SKYTH provides a comprehensive and extensible platform for advanced AI-powered experiences.



---

## ✨ Key Features

SKYTH is packed with features that go beyond a simple chatbot, creating a versatile tool for information discovery and creation.

-   **🧠 Agentic Core:** At its heart, SKYTH uses an LLM-based router to analyze user intent and dynamically select the most appropriate tool or pipeline, moving beyond simple keyword matching.
-   **📚 Multi-Layered Memory:** A persistent, multi-component memory system (`Core`, `Episodic`, `Semantic`, `Resource`) allows the AI to learn user preferences and maintain context across conversations.
-   **🌐 Real-Time Research:**
    -   **Standard Research:** Executes an LLM-planned, multi-step search strategy using DuckDuckGo for comprehensive answers.
    -   **Deep Research:** Scrapes and analyzes multiple web sources using `Trafilatura`, `BeautifulSoup`, and `Selenium` to automatically generate detailed, styled HTML reports, complete with embedded media and a downloadable PDF version.
-   **🎨 Dynamic & Multimodal Interaction:**
    -   **Image Analysis:** Understands and answers questions about user-uploaded images.
    -   **Image Editing:** Edits images based on user prompts (e.g., "add a hat to the person").
    -   **Image Generation:** Creates new images from text descriptions using Google's Gemini models.
    -   **File Analysis:** Extracts and analyzes text from PDFs and other documents.
    -   **Voice Synthesis:** Provides streaming text-to-speech audio using Microsoft Edge's high-quality neural voices.
    -   **Audio Transcription:** Transcribes user-uploaded audio files.
-   **📊 Interactive Visualizations:**
    -   **Data & Math:** Generates interactive HTML5 Canvas visualizations (using `p5.js`) for mathematical functions, physics concepts, or data.
    -   **Financial Data:** Fetches and displays real-time stock data in interactive `Chart.js` graphs.
-   **💻 Coding & HTML Assistant:**
    -   **Code Generation:** Writes, explains, and debugs code in various languages.
    -   **Live Previews:** Renders HTML, CSS, and JavaScript code directly in an `iframe` for immediate visual feedback.
-   **📰 Discover Page:** A built-in news aggregator that scrapes and displays articles from various categories, with a personalized "For You" section that learns from user interactions.
-   **👤 User-Centric Experience:**
    -   **Personas:** Switch between different AI personalities (e.g., `Academic`, `Coding`, `Unhinged`) for tailored responses.
    -   **Full Chat Management:** Secure user authentication (Google OAuth) with persistent, renameable, and deletable chat histories stored in a local SQLite database.

---

## 🛠️ Technology Stack

| Component         | Technology/Library                                                                                                             |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| **Backend**       | `Flask`, `Python 3.8+`                                                                                                         |
| **AI Models**     | `Google Gemini` (Flash, Pro, Vision, Image Generation)                                                                         |
| **Web Scraping**  | `Selenium`, `BeautifulSoup4`, `trafilatura`                                                                                    |
| **Web Search**    | `duckduckgo-search`                                                                                                            |
| **Databases**     | `SQLite` (for chat history & memory), `TinyDB` (for user sessions)                                                             |
| **Frontend**      | `HTML5`, `CSS3`, `JavaScript`, `Marked.js`, `Highlight.js`, `Anime.js`, `MathJax`                                              |
| **Visualizations**| `p5.js` & `Chart.js` (via dynamic HTML generation)                                                                             |
| **Stock Data**    | `Node.js`, `yahoo-finance2`                                                                                                    |
| **Audio**         | `edge-tts` (TTS), `SpeechRecognition` & `pydub` (Transcription)                                                                |
| **File Parsing**  | `pypdf` (PDFs)                                                                                                                 |
| **Authentication**| `Authlib` (Google OAuth)                                                                                                       |

---

## 🚀 Getting Started

Follow these steps to get your own instance of the SKYTH Engine running locally.

### Prerequisites

-   **Python 3.8+**: Ensure Python and `pip` are installed and accessible from your terminal.
-   **Node.js**: Required for the stock data fetching script. Install it from [nodejs.org](https://nodejs.org/).
-   **Chrome & Chromedriver**: Required for Selenium-based scraping. The Chromedriver version must match your installed Chrome browser. Download it from the [Chrome for Testing availability dashboard](https://googlechromelabs.github.io/chrome-for-testing/) and ensure it's in your system's `PATH`.
-   **FFmpeg**: Required for audio processing (`pydub`). Install it from [ffmpeg.org](https://ffmpeg.org/download.html) and ensure `ffmpeg` and `ffprobe` are in your system's `PATH`.
-   **API Keys**:
    -   **Google Gemini API Key**: The application relies heavily on Gemini for its core functionalities. Get a key from [Google AI Studio](https://aistudio.google.com/app/apikey).

### Installation

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/LinearJet/Skyth-Engine.git
    && cd Skyth-Engine
    ```

2.  **Set Up a Virtual Environment (Recommended):**
    ```bash
    # For Unix/macOS
    python3 -m venv venv
    source venv/bin/activate

    # For Windows
    python -m venv venv
    venv\Scripts\activate
    ```

3.  **Install Python Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Install Node.js Dependencies:**
    The stock data script uses a Node.js library. Navigate to the project root and run:
    ```bash
    npm install yahoo-finance2
    ```

5.  **Configure Environment Variables:**
    Create a `.env` file in the project root and add your API keys and a secret key for Flask sessions.
    ```env
    # .env
    GEMINI_API_KEY=your-gemini-api-key
    SECRET_KEY=generate-a-super-secret-random-string-here
    PORT=5000
    
    # For Google OAuth (Optional, but required for user login)
    GOOGLE_CLIENT_ID=your-google-client-id
    GOOGLE_CLIENT_SECRET=your-google-client-secret
    ```

### Running the Application

1.  **Initialize the Database:**
    The application will automatically create and initialize the `memory.db` SQLite database on the first run.

2.  **Start the Flask Server:**
    ```bash
    python app.py
    ```
    The server will start on `http://127.0.0.1:5000` (or the port specified in your `.env` file).

3.  **Access the Application:**
    Open your web browser and navigate to `http://127.0.0.1:5000`. You can now start interacting with the SKYTH Engine.

---

## 📜 License

This project is licensed under the **MIT License**.

The MIT License is a permissive free software license originating at the Massachusetts Institute of Technology (MIT). As a permissive license, it puts only very limited restrictions on reuse and has, therefore, high license compatibility.

You are free to:
-   **Share**: copy and redistribute the material in any medium or format.
-   **Adapt**: remix, transform, and build upon the material for any purpose, even commercially.

Under the following terms:
-   **Attribution**: You must give appropriate credit, provide a link to the license, and indicate if changes were made. You may do so in any reasonable manner, but not in any way that suggests the licensor endorses you or your use.

See the [LICENSE file](https://opensource.org/licenses/MIT) for the full text.

---

## 🤝 Contributing

Contributions are what make the open-source community such an amazing place to learn, inspire, and create. Any contributions you make are **greatly appreciated**.

If you have a suggestion that would make this better, please fork the repo and create a pull request. You can also simply open an issue with the tag "enhancement".

1.  Fork the Project
2.  Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3.  Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4.  Push to the Branch (`git push origin feature/AmazingFeature`)
5.  Open a Pull Request

---

## 📞 Contact

Project Maintainer: [LinearJet](https://github.com/LinearJet)

Project Link: [https://github.com/LinearJet/Skyth-Engine](https://github.com/LinearJet/Skyth-Engine)
