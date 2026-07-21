<div align="center">

# FitWright

### Built to fit.

The AI harness to build tailored resumes for each job application with Claude, ChatGPT, DeepSeek, Kimi, GLM, Gemma, and other LLMs. Supports both local and remote LLMs.

[How to Install](SETUP.md) - [Key Features](#key-features) - [Tech Stack](#tech-stack) - [Creator](#creators-note)

![version](https://img.shields.io/badge/Version-1.2-FFF?labelColor=F0F0E8&style=for-the-badge&color=1d4ed8) ![license](https://img.shields.io/badge/License-Apache%202.0-FFF?labelColor=F0F0E8&style=for-the-badge&color=1d4ed8)

</div>

## Getting Started

FitWright works by creating a master resume that you can use to tailor for each job application. Installation instructions here: [How to Install](#how-to-install)

### How It Works

1. **Upload** your master resume (PDF or DOCX)
2. **Paste** a job description you're targeting
3. **Review** AI-generated improvements and tailored content
4. **Cover Letter** and optional interview preparation for the job application
5. **Customize** the layout and sections to fit your style
6. **Export** as a professional PDF with your preferred template

## Key Features

### Core Features

**Master Resume**: Create a comprehensive master resume to draw from your existing one.

### Resume Builder

Paste in a job description and get an AI-powered resume tailored for that specific role.

You can:

- Modify suggested content
- Add/remove sections
- Rearrange sections via drag-and-drop
- Choose from multiple resume templates

### Cover Letter Generator

Generate tailored cover letters based on the job description and your resume.

### Interview Preparation

Generate structured, resume-grounded interview prep for saved tailored resumes. Use the Builder's Interview Prep tab on demand, or enable automatic generation in Settings.

### Resume Scoring & Keyword Highlighting

Analyze your resume against the job description with a match score, keyword highlighting, and suggestions for improvement.

### PDF Export

Export your tailored resume and cover letter as PDF.

### Templates

| Template Name | Preview | Description |
|---------------|---------|-------------|
| **Classic Single Column** | ![Classic Template](assets/pdf-templates/single-column.jpg) | A traditional and clean layout suitable for most industries. [View PDF](assets/pdf-templates/single-column.pdf) |
| **Modern Single Column** | ![Modern Template](assets/pdf-templates/modern-single-column.jpg) | A contemporary design with a focus on readability and aesthetics. [View PDF](assets/pdf-templates/modern-single-column.pdf)|
| **Classic Two Column** | ![Classic Two Column Template](assets/pdf-templates/two-column.jpg) | A structured layout that separates sections for clarity. [View PDF](assets/pdf-templates/two-column.pdf)|
| **Modern Two Column** | ![Modern Two Column Template](assets/pdf-templates/modern-two-column.jpg) | A sleek design that utilizes two columns for better organization. [View PDF](assets/pdf-templates/modern-two-column.pdf)|

### Internationalization

- **Multi-Language UI**: Interface available in English, Spanish, Chinese, Japanese, French, and Portuguese (Brazilian)
- **Multi-Language Content**: Generate resumes and cover letters in your preferred language

### Roadmap

If you have any suggestions or feature requests, please feel free to open an issue on GitHub.

- AI Canvas for crafting impactful, metric-driven resume content
- Email template generator for job applications
- Multi-job description optimization

<a id="how-to-install"></a>

## How to Install

For detailed setup instructions, see **[SETUP.md](SETUP.md)**.

### Prerequisites

| Tool | Version | Installation |
|------|---------|--------------|
| Python | 3.13+ | [python.org](https://python.org) |
| Node.js | 22+ | [nodejs.org](https://nodejs.org) |
| uv | Latest | [astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/) |

### Quick Start

Fastest for MacOS, WSL and Ubuntu users:

```bash
# Clone the repository
git clone https://github.com/ObaidGits/FitWRight.git
cd FitWRight

# Backend (Terminal 1)
cd apps/backend
cp .env.example .env        # Configure your AI provider
uv sync                      # Install dependencies
uv run app

# Frontend (Terminal 2)
cd apps/frontend
npm install
npm run dev
```

Open **<http://localhost:3000>** and configure your AI provider in Settings.

### Supported AI Providers

| Provider | Local/Cloud | Notes |
|----------|-------------|-------|
| **Ollama** | Local | Free, runs on your machine |
| **OpenAI** | Cloud | GPT-5 Nano, GPT-4o |
| **Anthropic** | Cloud | Claude Haiku 4.5 |
| **Google Gemini** | Cloud | Gemini 3 Flash |
| **OpenRouter** | Cloud | Access to multiple models |
| **DeepSeek** | Cloud | DeepSeek Chat |

### Docker Deployment

Build and run on a single public port (`3000`) with the API available at `/api`:

```bash
docker compose up -d
```

Endpoints:

- App: <http://localhost:3000>
- API health check: <http://localhost:3000/api/v1/health>
- API docs: <http://localhost:3000/docs>

> **Using Ollama with Docker?** Use `http://host.docker.internal:11434` as the Ollama URL instead of `localhost`.

### Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | FastAPI, Python 3.13+, LiteLLM |
| Frontend | Next.js 16, React 19, TypeScript |
| Database | TinyDB (JSON file storage) |
| Styling | Tailwind CSS 4, Swiss International Style |
| PDF | Headless Chromium via Playwright |

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](.github/CONTRIBUTING.md) to get started, and feel free to open an issue or discussion on GitHub.

<a id="creators-note"></a>

## Creator's Note

Thank you for checking out FitWright. If you want to connect, collaborate, or just say hi, feel free to reach out!
~ **Obaidullah Zeeshan**

- Website: [https://obaidullah-zeeshan.dev](https://obaidullah-zeeshan.dev/)
- LinkedIn: [https://www.linkedin.com/in/obaidullah-zeeshan/](https://www.linkedin.com/in/obaidullah-zeeshan/)
- GitHub: [https://github.com/ObaidGits](https://github.com/ObaidGits)

## License

FitWright is released under the [Apache License 2.0](LICENSE).
