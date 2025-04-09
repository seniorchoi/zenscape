# Project Title

Because who doesn’t like a dash of creativity?

## Table of Contents
1. [Setting Up the Virtual Environment](#setting-up-the-virtual-environment)  
2. [Installing Requirements](#installing-requirements)  
3. [Configuration & Environment Variables](#configuration--environment-variables)  
4. [Running the Application](#running-the-application)  

---

## Setting Up the Virtual Environment

First, let’s create and activate your virtual environment:

    python -m venv venv
    source venv/bin/activate

If your shell disagrees, show it some love—follow these commands step by step, and it’ll come around.

---

## Installing Requirements

Next, install the required Python packages:

    pip install -r requirements.txt

Done in a jiffy—faster than you can say "AI to the rescue!"

---

## Configuration & Environment Variables

Create a new file named `.env` and include the following:

    OPENAI_API_KEY=
    ELEVENLABS_API_KEY=

You can grab your API keys here:
- [OpenAI API Keys](https://platform.openai.com/settings/organization/api-keys)
- [Eleven Labs API Keys](https://elevenlabs.io/)

**Pro tip:** Never share these keys in a public place. Evil minions might be lurking.

---

## Running the Application

Finally, you can run the app in one of two ways:

    flask run

or

    python3 app.py

Feel free to do a happy dance now—you’ve officially conquered the setup!
