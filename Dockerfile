# 1. Start with a lightweight version of Python
FROM python:3.11-slim

# 2. Create a folder inside the cloud server called /app
WORKDIR /app

# 3. Copy your requirements.txt file over first
COPY requirements.txt .

# 4. Install all your Python modules (discord.py, flask, firebase-admin)
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy the rest of your bot's code into the server
COPY . .

# 6. Tell the server to open port 8080 for your keep-alive webserver
EXPOSE 8080

# 7. Press the "Play" button! (Make sure your main file is named main.py)
CMD ["python", "main.py"]

# THIS IS VIBECODED FULLY LOL, I DO NOT HAVE EXPERIENCE WITH DOCKER AND JUST WANT TO RUN THE BOT ON Back4App