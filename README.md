# Oryxie, the Qatari Virtual Bot Setup Guide

---

## 1. Install Required Tools

- **Visual Studio Code (VSC)**  
  Download and install:  
  https://code.visualstudio.com/download

- **Python**  
  Download and install:  
  https://www.python.org/downloads/

- **Git**  
  Download and install:  
  https://git-scm.com/downloads/win

---

## 2. Create a GitHub Account

Sign up for a free account:  
https://github.com/signup

---

## 3. Set Up Visual Studio Code with GitHub

- Open **Visual Studio Code**
- Sign in with your GitHub account (bottom-left corner or via Command Palette)

---

## 4. Clone the Project

1. Create a folder on your computer where you want to keep the project.
2. Open a terminal (or use VSC terminal) and run:

```bash
git clone https://github.com/Bumyy/Oryxie.git
```

3. Open the folder in VSC.

---

## 5. Branch Setup & Working with Git

- In VSC’s left panel, click **Source Control** (git icon).
- Under **Repositories**, find the repo named `Oryxie`.
- On the top right of the Source Control tab, click the branch selector (default is `main`).
- Switch to the `dev` branch — **this is where you will be working**.
- `main` is reserved for production.

---

## 6. Editing and Committing Changes

- Go to the **Explorer** tab to browse and edit files.
- Open and modify a file, e.g., `cogs/pingpong.py`.
- Go back to **Source Control** to see your changes.
- Click the **+ (plus)** icon next to the changed files to stage them.
- Write a descriptive **commit message** in the message box.
- Click the **checkmark (✓ Commit)** above to commit your changes and push them to GitHub (Sync Changes).
- Your changes will now be visible to the whole team.

---

## 7. Environment Variable Setup

- In the Explorer tab, **copy** the `.env.example` file and **paste** it in the same folder.
- Rename the copied file to `.env` (remove `.example`).
- This `.env` file will store your **Discord bot token** and **development server Guild ID**.

---

## 8. Create a Development Discord Bot

- Go to Discord Developer Portal:
  [https://discord.com/developers/applications](https://discord.com/developers/applications)
- Create a new Application.
- Navigate to the **Bot** tab on the left.
- Scroll down and click **Reset Token** (or **Copy Token** if available).
- Paste the token into your `.env` file:

```
DISCORD_TOKEN=YOUR_BOT_TOKEN_HERE
```

- Under **OAuth2 > URL Generator**, generate an invite link for your bot with the necessary permissions.
- Invite the bot to the **Qatari Staff** test server (we test bots there, not on the main server).

---

## 9. Get the Guild ID

- Ask Bumy for the **Guild ID** of the test server.
- Paste it into your `.env` file like this:

```
GUILD_ID=YOUR_TEST_SERVER_ID_HERE
```

---

## 10. Running the Bot

- Make sure you have Python dependencies installed:

```bash
pip install -r requirements.txt
```

- Run the bot:

```bash
python bot.py
```

- The bot will connect to Discord and sync slash commands in the test guild.

---

## Route-Ready Pilot DMs

The bot sends a direct message to assigned next-leg pilots after it detects that
the aircraft has landed and is parked at the next route's origin. The message
includes the route and takeoff deadline: **6 hours at an airline hub** or
**12 hours at a non-hub airport**. The route-ready cog is loaded automatically.

Before enabling this feature in a deployment, apply
[`database/route_ready_notifications_mysql_schema.sql`](./database/route_ready_notifications_mysql_schema.sql)
to the bot database. Route-ready messages require the pilot's Discord account
to be linked and their DMs to be open.

Stopped-flight, route-ready, and Oryxpace task-notification DMs are reported to
the channel configured by `STOPPED_FLIGHT_DM_LOG_CHANNEL_ID`, including delivery
status and recipient. Other bot messages posted in channels or as interaction
responses are not DMs and are not included in these reports.

Route-exhaustion alerts also include an estimated demand label based on Infinite
Flight aircraft activity: **High Demand** means at least 3 flights in the last
7 days; otherwise it is **Low Demand**. If recent activity cannot be checked,
the alert says **Demand unknown** rather than guessing.

---

## Summary

- Clone repo → Switch to `dev` branch
- Set up `.env` with your bot token & guild ID
- Edit code in `cogs/` folder
- Stage, commit, and push your changes via Git
- Run the bot locally for testing

---

If you get stuck or need help, just ask! Happy coding 🚀
