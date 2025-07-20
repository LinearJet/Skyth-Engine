**SKYTH Engine**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Overview
SKYTH is an advanced AI-powered web application designed to deliver real-time research, dynamic visualizations, deep insights, and voice synthesis capabilities. It integrates multiple AI models and tools to provide a robust platform for information retrieval, content generation, and interactive experiences. The application is built with Flask, leveraging a modular architecture to handle web scraping, natural language processing, image generation, and more.
Features

Real-Time Research: Utilizes DuckDuckGo search, YouTube video search, and web scraping (via BeautifulSoup, Trafilatura, and Selenium) to fetch and analyze information.
Multimodal AI: Supports text, image, and file analysis using Google Gemini models for conversational, reasoning, visualization, and image generation tasks.
Dynamic Visualizations: Generates interactive HTML5 canvas visualizations using p5.js for mathematical, scientific, or general data representations.
Voice Synthesis: Integrates Microsoft Edge TTS for text-to-speech streaming.
Deep Research Pipeline: Conducts comprehensive research with automated source analysis, visual content curation, and report generation in HTML, Markdown, and PDF formats.
Discover Page: Curates trending news topics and articles with a tiered scraping system for fast and reliable content extraction.
File Handling: Supports image uploads, audio transcription (via SpeechRecognition), and PDF content analysis (via pypdf).
Customizable Interface: Offers user personalization with themes, profile pictures, and custom personas.

Prerequisites

Python 3.8+: Ensure Python is installed on your system.
Chromedriver: Required for Selenium-based scraping. Must be compatible with your installed Chrome browser version and added to your system PATH.
FFmpeg: Required for audio processing with pydub. Install it and ensure it's accessible in your system PATH.
API Keys:
Google Gemini API Key: Set the `GEMINI_API_KEY` environment variable for conversational, reasoning, visualization, and image generation models.


Dependencies: Install all required Python packages listed in requirements.txt.

Installation

Clone the Repository:
`git clone https://github.com/LinearJet/Skyth-Engine.git
&&  cd skyth`


Set Up a Virtual Environment (recommended):
`python -m venv venv`
`source venv/bin/activate  # On Windows: venv\Scripts\activate`


Install Dependencies:
``pip install -r requirements.txt``


Configure Environment Variables:Create a .env file in the project root and add your API key:
`GEMINI_API_KEY=your-gemini-api-key
PORT=5000  # Optional: specify the port for Flask`


Install Chromedriver:

Download the appropriate Chromedriver version for your Chrome browser from here.
Add Chromedriver to your system PATH or place it in the project directory.


Install FFmpeg:

Download and install FFmpeg from ffmpeg.org.
Ensure ffmpeg and ffprobe are accessible in your system PATH.



Running the Application

Start the Flask Server:
`python app.py`

The server will run on http://127.0.0.1:5000 by default (or the port specified in the .env file).

Access the Application:

Open a web browser and navigate to http://127.0.0.1:5000.
Explore the homepage, discover trending topics, or interact with the AI through the search endpoint.



Usage

Homepage (/): Displays the main interface with options to enter queries, view popular topics, and customize the user experience.
Discover Page (/discover): Browse curated articles across categories like Sports, Technology, and Entertainment.
API Endpoints:
/api/parse_article: POST a URL to extract article content using Trafilatura or BeautifulSoup.
/api/upload_image: POST an image file to receive a Base64-encoded data URI for analysis.
/api/transcribe_audio: POST an audio file to transcribe it using SpeechRecognition and Google’s speech-to-text API.
/api/tts: POST text and a persona to generate streaming text-to-speech audio using Edge TTS.
/popular_topics: GET trending news topics, cached for performance.
/fetch_articles/<category>: GET articles for a specific category (e.g., Technology, Sports).
/get_full_article: POST a URL to scrape full article content using a tiered scraping system.
/track_interaction: POST user interactions to improve the "For You" category recommendations.



Project Structure

app.py: Main Flask application with routing, AI pipelines, and scraping logic.
index.html: Frontend template for the main interface, including personalization and interaction features.
requirements.txt: List of Python dependencies.
static/: Static assets like favicon.ico and robots.txt.
templates/: HTML templates for rendering pages.
memory.db: SQLite database for storing query history (created automatically on first run).

Notes

Performance: The application uses caching for article lists and trending topics to reduce API calls and improve response times.
Error Handling: Robust error handling is implemented for scraping, API calls, and file processing. Check logs for detailed error messages.
Dependencies: Ensure all dependencies in requirements.txt are installed. Some features (e.g., audio transcription) require external tools like FFmpeg.
API Key Security: Store your Gemini API key securely in the .env file and avoid committing it to version control.
Selenium: Requires a compatible Chromedriver. If you encounter issues, verify the Chromedriver version and PATH configuration.
Audio Transcription: The /api/transcribe_audio endpoint uses Google’s free speech-to-text API via SpeechRecognition. Ensure FFmpeg is installed for audio format conversion.

Limitations

API Key Dependency: The application requires a valid Google Gemini API key for most AI functionalities.
Selenium Performance: Selenium-based scraping can be slow and resource-intensive; it’s used as a fallback for robust content extraction.
Audio Transcription: Relies on Google’s web-based speech recognition, which may have limitations in noisy environments or with certain audio formats.
Image Generation: Currently uses Gemini for image generation, with Pollinations.ai as a fallback. Some prompts may be rejected due to content policies.
Browser Compatibility: Visualizations and HTML previews are designed for modern browsers (Chrome, Firefox, Edge).

Contributing
Contributions are welcome! Please submit a pull request or open an issue for bug reports, feature requests, or improvements.
License
This project is licensed under the MIT License. See the LICENSE file for details.
Contact
For support or inquiries, contact the project maintainers via GitHub issues.
