# Supernote Cloud Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![test_suite](https://github.com/allenporter/home-assistant-supernote-cloud/actions/workflows/test.yaml/badge.svg)](https://github.com/allenporter/home-assistant-supernote-cloud/actions)

Bring your Ratta Supernote e-ink tablet into your smart home ecosystem. This integration connects Home Assistant to a **Supernote Private Cloud**, allowing you to monitor device storage capacity, browse notebook pages dynamically, and interact with your handwritten notes using Large Language Models (LLMs) and Home Assistant Assist.

It is designed to pair natively with both the official [Supernote Private Cloud](https://support.supernote.com/en_US/Whats-New/setting-up-your-own-supernote-private-cloud-beta) and your own self-hosted **[Supernote Personal Knowledge Hub](https://github.com/allenporter/supernote)** with built-in AI features (such as notebook transcription and semantic search).

---

## 🌟 Key Features

### 🧠 AI & LLM Assistants

This integration registers custom tools with Home Assistant's LLM engine, enabling conversational agents (such as Google Gemini in Home Assistant) to read, search, and answer questions about your notes.

- **Semantic Search (`search_supernote`)**: Allows LLMs to search for concepts across your handwritten notebook library using vector similarity search.
- **Notebook Transcripts (`get_supernote_transcript`)**: Exposes the text transcript of any notebook or journal to the model.
- **LLM Agent Compatibility**: Pairs with Home Assistant LLM integrations (such as Google Gemini, Anthropic Claude, OpenAI, etc.) to query, summarize, or search your handwritten notes using natural language.

### 🖼️ Dynamic Notebook Rendering (Media Source)

Exposes your notebook library via Home Assistant's native **Media Browser**.

- Browse folders and files synced from your tablet.
- View notebook pages rendered dynamically on-the-fly as `.png` images directly in the Home Assistant UI or cast them to media players.

### ✅ To-Do Lists

Synchronizes your Supernote To-Do lists directly with Home Assistant's native **To-do** integration (and Home Assistant Assist):

- Exposes your default **Inbox** as well as custom Supernote task lists, automatically discovering newly created lists and removing deleted ones.
- View, create, rename, complete/reopen, and delete tasks with support for descriptions, due dates, and due datetimes.

### 📊 Storage Capacity Sensors

Keep track of your cloud account's storage capacity. The integration registers four sensors polled automatically every 30 minutes:

- **Storage Used** (in GB)
- **Storage Total** (in GB)
- **Storage Free** (in GB)
- **Storage Usage Ratio** (in %)

---

## 🔒 Supported Cloud Options

This integration supports any sync server implementing the [OpenAPI Specification](https://github.com/allenporter/supernote/tree/main/api-spec) for the Supernote Private Cloud protocol. Compatible options include:

- **Supernote Personal Knowledge Hub (Recommended)**: Pairs with **[allenporter/supernote](https://github.com/allenporter/supernote)**, a self-hosted, SQLite-backed private cloud server with built-in Gemini AI features.
  - **Local Ownership**: Sync your notes directly to a local database running in your home lab, NAS, or private server.
  - **AI Integration**: Unlocks semantic search and note transcription features for LLM tools.
  - **Highly Efficient**: Consumes less than ~200MB of memory at idle.
- **Official Supernote Private Cloud**: Supports Ratta's official [Supernote Private Cloud](https://support.supernote.com/en_US/Whats-New/setting-up-your-own-supernote-private-cloud-beta) sync service.

---

## 🚀 Getting Started

### Installation via HACS

- Open **HACS** in Home Assistant.
- Click the three dots in the top right and select **Custom repositories**.
- Add `https://github.com/allenporter/home-assistant-supernote-cloud` with category **Integration**.
- Click **Download**.
- Restart Home Assistant.

### Configuration

- Go to **Settings** -> **Devices & Services** -> **Add Integration**.
- Search for **Supernote Cloud**.
- Enter your credentials (username/phone number and password).
- Enter the **Supernote Private Cloud URL** of your self-hosted server (this defaults to the standard cloud URL if left unchanged).
- Follow the setup flow (including entering the SMS verification code if applicable).

---

## 🛠️ Local Development

### Pre-requisites

Set up the python virtual environment and bootstrap development scripts:

```bash
$ script/bootstrap
$ script/setup
```

### Running the Dev Server

Launch Home Assistant locally with the custom component loaded:

```bash
$ script/server
```

### Running Tests

We use `pytest` for unit testing. Run the test suite:

```bash
$ .venv/bin/pytest
```
