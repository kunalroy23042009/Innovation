"""
Installer.py — Universal AI-Powered Software Installer
========================================================
Installs virtually ANY software on Windows / Linux / macOS.

Resolution strategy (layered, most reliable first):
  1. Local fuzzy match against comprehensive built-in app database
     (handles typos, abbreviations, slang — no LLM needed)
  2. LLM enhancement (Groq → Ollama) for apps not in the local DB
  3. Live package manager search as final safety net

Install chain per OS:
  Windows : winget → MS Store → chocolatey → scoop → npm → cargo → go → pip
  Linux   : apt → snap → flatpak → dnf/yum → pacman/yay → zypper → npm → cargo → go → pip
  macOS   : brew cask → brew formula → mas → npm → cargo → go → pip
"""

import subprocess
import sys
import shutil
import platform
import re
import json
from dataclasses import dataclass, field
from typing import Optional
from difflib import get_close_matches

from logger import logger, log_step

OS = platform.system()


# ══════════════════════════════════════════════════════════════════════════════
# BUILT-IN APP DATABASE
# Key = all aliases/typos that should match this app (lowercase)
# Value = package IDs for every package manager
# ══════════════════════════════════════════════════════════════════════════════

APP_DB: dict[str, dict] = {
    # ── Browsers ──────────────────────────────────────────────────────────────
    "chrome|google chrome|googlechrome|chroem|chrme|gogle chrome": {
        "canonical": "Google Chrome",
        "winget": "Google.Chrome", "msstore": "XPFCG9B3T4SFDR",
        "choco": "googlechrome", "scoop": "extras/googlechrome",
        "apt": "google-chrome-stable", "snap": "chromium",
        "flatpak": "com.google.Chrome",
        "brew_cask": "google-chrome", "category": "browser",
        "download": "https://www.google.com/chrome/",
    },
    "firefox|mozila firefox|fierfox|firfox|ffox": {
        "canonical": "Mozilla Firefox",
        "winget": "Mozilla.Firefox", "choco": "firefox", "scoop": "extras/firefox",
        "apt": "firefox", "snap": "firefox", "flatpak": "org.mozilla.firefox",
        "brew_cask": "firefox", "category": "browser",
        "download": "https://www.mozilla.org/firefox/",
    },
    "brave|brave browser|brav": {
        "canonical": "Brave Browser",
        "winget": "Brave.Brave", "choco": "brave", "scoop": "extras/brave",
        "apt": "brave-browser", "flatpak": "com.brave.Browser",
        "brew_cask": "brave-browser", "category": "browser",
        "download": "https://brave.com/download/",
    },
    "edge|microsoft edge|msedge": {
        "canonical": "Microsoft Edge",
        "winget": "Microsoft.Edge", "choco": "microsoft-edge",
        "apt": "microsoft-edge-stable", "brew_cask": "microsoft-edge",
        "category": "browser", "download": "https://www.microsoft.com/edge",
    },
    "opera|opra": {
        "canonical": "Opera",
        "winget": "Opera.Opera", "choco": "opera", "apt": "opera-stable",
        "brew_cask": "opera", "category": "browser",
        "download": "https://www.opera.com/download",
    },
    "tor|tor browser|torbrowser": {
        "canonical": "Tor Browser",
        "winget": "TorProject.TorBrowser", "choco": "tor-browser",
        "flatpak": "org.torproject.torbrowser-launcher",
        "brew_cask": "tor-browser", "category": "browser",
        "download": "https://www.torproject.org/download/",
    },

    # ── Communication ──────────────────────────────────────────────────────────
    "discord|discrod|discort|discrd|disord": {
        "canonical": "Discord",
        "winget": "Discord.Discord", "msstore": "XPDC2RH70K22MN",
        "choco": "discord", "scoop": "extras/discord",
        "apt": "discord", "snap": "discord", "flatpak": "com.discordapp.Discord",
        "brew_cask": "discord", "category": "communication",
        "download": "https://discord.com/download",
    },
    "slack|slak|slck": {
        "canonical": "Slack",
        "winget": "SlackTechnologies.Slack", "msstore": "9WZDNCRDK8WW",
        "choco": "slack", "snap": "slack --classic",
        "flatpak": "com.slack.Slack",
        "brew_cask": "slack", "category": "communication",
        "download": "https://slack.com/downloads",
    },
    "zoom|zooom|zom": {
        "canonical": "Zoom",
        "winget": "Zoom.Zoom", "choco": "zoom", "scoop": "extras/zoom",
        "apt": "zoom", "flatpak": "us.zoom.Zoom",
        "brew_cask": "zoom", "category": "communication",
        "download": "https://zoom.us/download",
    },
    "teams|microsoft teams|ms teams|msteams": {
        "canonical": "Microsoft Teams",
        "winget": "Microsoft.Teams", "msstore": "XPFBBS0ZS9RGPD",
        "choco": "microsoft-teams", "apt": "teams",
        "snap": "teams", "flatpak": "com.microsoft.Teams",
        "brew_cask": "microsoft-teams", "category": "communication",
        "download": "https://www.microsoft.com/microsoft-teams/download-app",
    },
    "telegram|telgram|telegramm": {
        "canonical": "Telegram",
        "winget": "Telegram.TelegramDesktop", "msstore": "9NZTWSQNTD0S",
        "choco": "telegram", "scoop": "extras/telegram",
        "apt": "telegram-desktop", "snap": "telegram-desktop",
        "flatpak": "org.telegram.desktop",
        "brew_cask": "telegram", "mas": "747648890",
        "category": "communication", "download": "https://telegram.org/",
    },
    "whatsapp|watsapp|whatsap": {
        "canonical": "WhatsApp",
        "winget": "WhatsApp.WhatsApp", "msstore": "9NKSQGP7F2NH",
        "choco": "whatsapp", "flatpak": "io.github.mimbrero.WhatsAppDesktop",
        "brew_cask": "whatsapp", "mas": "1147396723",
        "category": "communication", "download": "https://www.whatsapp.com/download",
    },
    "skype|skipe|skyp": {
        "canonical": "Skype",
        "winget": "Microsoft.Skype", "msstore": "9WZDNCRFJ364",
        "choco": "skype", "apt": "skypeforlinux",
        "snap": "skype --classic", "flatpak": "com.skype.Client",
        "brew_cask": "skype", "mas": "304878510",
        "category": "communication", "download": "https://www.skype.com/download",
    },
    "signal|sigal": {
        "canonical": "Signal",
        "winget": "OpenWhisperSystems.Signal", "choco": "signal",
        "apt": "signal-desktop", "flatpak": "org.signal.Signal",
        "brew_cask": "signal", "category": "communication",
        "download": "https://signal.org/download/",
    },

    # ── Dev Tools ──────────────────────────────────────────────────────────────
    "vscode|vs code|visual studio code|vscoed|vsocde|vscode|vscde|vscoe": {
        "canonical": "Visual Studio Code",
        "winget": "Microsoft.VisualStudioCode", "msstore": "XP9KHM4BK9FZ7Q",
        "choco": "vscode", "scoop": "extras/vscode",
        "apt": "code", "snap": "code --classic",
        "flatpak": "com.visualstudio.code",
        "brew_cask": "visual-studio-code", "category": "dev_tool",
        "download": "https://code.visualstudio.com/download",
    },
    "git|gitt|gti": {
        "canonical": "Git",
        "winget": "Git.Git", "choco": "git", "scoop": "git",
        "apt": "git", "dnf": "git", "pacman": "git",
        "brew_formula": "git", "category": "dev_tool",
        "download": "https://git-scm.com/downloads",
    },
    "github desktop|githubdesktop|gh desktop": {
        "canonical": "GitHub Desktop",
        "winget": "GitHub.GitHubDesktop", "choco": "github-desktop",
        "scoop": "extras/github",
        "brew_cask": "github", "category": "dev_tool",
        "download": "https://desktop.github.com/",
    },
    "nodejs|node|node.js|nod|ndoe": {
        "canonical": "Node.js",
        "winget": "OpenJS.NodeJS", "choco": "nodejs", "scoop": "nodejs",
        "apt": "nodejs", "snap": "node --classic",
        "dnf": "nodejs", "pacman": "nodejs",
        "brew_formula": "node", "category": "dev_tool",
        "download": "https://nodejs.org/download/",
    },
    "python|python3|pyhton|pytohn|pythn": {
        "canonical": "Python 3",
        "winget": "Python.Python.3", "msstore": "9PJPW5LDXLZ5",
        "choco": "python", "scoop": "python",
        "apt": "python3", "dnf": "python3", "pacman": "python",
        "brew_formula": "python", "category": "dev_tool",
        "download": "https://www.python.org/downloads/",
    },
    "java|jdk|java development kit|jva": {
        "canonical": "Java JDK",
        "winget": "Oracle.JDK.21", "choco": "openjdk", "scoop": "extras/openjdk",
        "apt": "default-jdk", "dnf": "java-latest-openjdk",
        "pacman": "jdk-openjdk",
        "brew_formula": "openjdk", "category": "dev_tool",
        "download": "https://www.java.com/download/",
    },
    "rust|rustlang|rust language": {
        "canonical": "Rust",
        "winget": "Rustlang.Rustup", "choco": "rust",
        "apt": "rustup", "dnf": "rust", "pacman": "rust",
        "brew_formula": "rust", "category": "dev_tool",
        "download": "https://www.rust-lang.org/tools/install",
    },
    "go|golang|go lang": {
        "canonical": "Go",
        "winget": "GoLang.Go", "choco": "golang", "scoop": "go",
        "apt": "golang", "dnf": "golang", "pacman": "go",
        "brew_formula": "go", "category": "dev_tool",
        "download": "https://go.dev/dl/",
    },
    "ruby|rubyy": {
        "canonical": "Ruby",
        "winget": "RubyInstallerTeam.Ruby", "choco": "ruby",
        "apt": "ruby", "dnf": "ruby", "pacman": "ruby",
        "brew_formula": "ruby", "category": "dev_tool",
        "download": "https://www.ruby-lang.org/downloads/",
    },
    "docker|dokcer|docekr|docker desktop": {
        "canonical": "Docker Desktop",
        "winget": "Docker.DockerDesktop", "choco": "docker-desktop",
        "apt": "docker.io", "snap": "docker",
        "flatpak": "io.docker.DockerDesktop",
        "brew_cask": "docker", "category": "dev_tool",
        "download": "https://www.docker.com/products/docker-desktop/",
    },
    "postman|postamn|potsman": {
        "canonical": "Postman",
        "winget": "Postman.Postman", "choco": "postman",
        "apt": "postman", "snap": "postman",
        "flatpak": "com.getpostman.Postman",
        "brew_cask": "postman", "category": "dev_tool",
        "download": "https://www.postman.com/downloads/",
    },
    "insomnia|insomnnia": {
        "canonical": "Insomnia",
        "winget": "Insomnia.Insomnia", "choco": "insomnia-rest-api-client",
        "flatpak": "rest.insomnia.Insomnia",
        "brew_cask": "insomnia", "category": "dev_tool",
        "download": "https://insomnia.rest/download",
    },
    "android studio|androidstudio|android stdio": {
        "canonical": "Android Studio",
        "winget": "Google.AndroidStudio", "choco": "androidstudio",
        "flatpak": "com.google.AndroidStudio",
        "brew_cask": "android-studio", "category": "dev_tool",
        "download": "https://developer.android.com/studio",
    },
    "cmake|cmkae": {
        "canonical": "CMake",
        "winget": "Kitware.CMake", "choco": "cmake",
        "apt": "cmake", "dnf": "cmake", "pacman": "cmake",
        "brew_formula": "cmake", "category": "dev_tool",
        "download": "https://cmake.org/download/",
    },
    "ffmpeg|ffmepg|ffmeg": {
        "canonical": "FFmpeg",
        "winget": "Gyan.FFmpeg", "choco": "ffmpeg", "scoop": "ffmpeg",
        "apt": "ffmpeg", "dnf": "ffmpeg", "pacman": "ffmpeg",
        "brew_formula": "ffmpeg", "category": "dev_tool",
        "download": "https://ffmpeg.org/download.html",
    },
    "kubectl|kubctl|kubectll": {
        "canonical": "kubectl",
        "winget": "Kubernetes.kubectl", "choco": "kubernetes-cli", "scoop": "kubectl",
        "apt": "kubectl", "brew_formula": "kubectl",
        "cargo": "", "npm": "",
        "category": "dev_tool", "download": "https://kubernetes.io/docs/tasks/tools/",
    },
    "terraform|terrafrom": {
        "canonical": "Terraform",
        "winget": "Hashicorp.Terraform", "choco": "terraform", "scoop": "terraform",
        "apt": "terraform", "brew_formula": "terraform",
        "category": "dev_tool", "download": "https://developer.hashicorp.com/terraform/install",
    },
    "gh|github cli|githubcli": {
        "canonical": "GitHub CLI",
        "winget": "GitHub.cli", "choco": "gh", "scoop": "gh",
        "apt": "gh", "dnf": "gh", "pacman": "github-cli",
        "brew_formula": "gh", "category": "dev_tool",
        "download": "https://cli.github.com/",
    },
    "ollama|olama|ollamma": {
        "canonical": "Ollama",
        "winget": "Ollama.Ollama", "choco": "ollama",
        "apt": "ollama", "brew_cask": "ollama",
        "category": "dev_tool", "download": "https://ollama.com/download",
    },
    "wsl|wsl2|windows subsystem for linux": {
        "canonical": "WSL2",
        "winget": "Microsoft.WSL", "choco": "wsl2",
        "category": "system_tool",
        "download": "https://learn.microsoft.com/windows/wsl/install",
    },

    # ── Databases ──────────────────────────────────────────────────────────────
    "postgresql|postgres|postgre|postgressql|psql": {
        "canonical": "PostgreSQL",
        "winget": "PostgreSQL.PostgreSQL", "choco": "postgresql",
        "apt": "postgresql", "dnf": "postgresql-server", "pacman": "postgresql",
        "brew_formula": "postgresql", "category": "database",
        "download": "https://www.postgresql.org/download/",
    },
    "pgadmin|pgadmin4|pg admin": {
        "canonical": "pgAdmin 4",
        "winget": "PostgreSQL.pgAdmin", "choco": "pgadmin4",
        "apt": "pgadmin4", "brew_cask": "pgadmin4",
        "category": "database", "download": "https://www.pgadmin.org/download/",
    },
    "mysql|mysqul|my sql": {
        "canonical": "MySQL",
        "winget": "Oracle.MySQL", "choco": "mysql",
        "apt": "mysql-server", "dnf": "mysql-server", "pacman": "mysql",
        "brew_formula": "mysql", "category": "database",
        "download": "https://dev.mysql.com/downloads/",
    },
    "mongodb|mongo db|mongdb|mangodb": {
        "canonical": "MongoDB",
        "winget": "MongoDB.Server", "choco": "mongodb",
        "apt": "mongodb", "dnf": "mongodb-org",
        "brew_formula": "mongodb-community", "category": "database",
        "download": "https://www.mongodb.com/try/download/community",
    },
    "redis|rediss|rdis": {
        "canonical": "Redis",
        "winget": "Redis.Redis", "choco": "redis-64",
        "apt": "redis-server", "dnf": "redis", "pacman": "redis",
        "brew_formula": "redis", "category": "database",
        "download": "https://redis.io/download/",
    },
    "sqlite|sqllite|sq lite": {
        "canonical": "SQLite",
        "winget": "SQLite.SQLite", "choco": "sqlite",
        "apt": "sqlite3", "dnf": "sqlite", "pacman": "sqlite",
        "brew_formula": "sqlite", "category": "database",
        "download": "https://www.sqlite.org/download.html",
    },

    # ── Media ──────────────────────────────────────────────────────────────────
    "vlc|vls|vlc media player|vlic": {
        "canonical": "VLC Media Player",
        "winget": "VideoLAN.VLC", "msstore": "XPDM1ZW6815MQM",
        "choco": "vlc", "scoop": "vlc",
        "apt": "vlc", "snap": "vlc", "flatpak": "org.videolan.VLC",
        "brew_cask": "vlc", "mas": "126153474",
        "category": "media", "download": "https://www.videolan.org/vlc/",
    },
    "spotify|spotfy|spottify|spotify": {
        "canonical": "Spotify",
        "winget": "Spotify.Spotify", "msstore": "9NCBCSZSJRSB",
        "choco": "spotify", "scoop": "extras/spotify",
        "apt": "spotify-client", "snap": "spotify",
        "flatpak": "com.spotify.Client",
        "brew_cask": "spotify", "mas": "324684580",
        "category": "media", "download": "https://www.spotify.com/download/",
    },
    "obs|obs studio|obstudio|obs-studio": {
        "canonical": "OBS Studio",
        "winget": "OBSProject.OBSStudio", "choco": "obs-studio",
        "apt": "obs-studio", "flatpak": "com.obsproject.Studio",
        "brew_cask": "obs", "category": "media",
        "download": "https://obsproject.com/download",
    },
    "audacity|audacsity|audacty|audicity": {
        "canonical": "Audacity",
        "winget": "Audacity.Audacity", "choco": "audacity",
        "apt": "audacity", "flatpak": "org.audacityteam.Audacity",
        "brew_cask": "audacity", "category": "media",
        "download": "https://www.audacityteam.org/download/",
    },
    "handbrake|hand brake|handbreak": {
        "canonical": "HandBrake",
        "winget": "HandBrake.HandBrake", "choco": "handbrake",
        "apt": "handbrake", "flatpak": "fr.handbrake.ghb",
        "brew_cask": "handbrake", "category": "media",
        "download": "https://handbrake.fr/downloads.php",
    },
    "kdenlive|kden live": {
        "canonical": "Kdenlive",
        "winget": "KDE.Kdenlive", "choco": "kdenlive",
        "apt": "kdenlive", "flatpak": "org.kde.kdenlive",
        "brew_cask": "kdenlive", "category": "media",
        "download": "https://kdenlive.org/download/",
    },
    "davinci resolve|davinci|davinchi resolve|davnci": {
        "canonical": "DaVinci Resolve",
        "winget": "Blackmagic.DaVinciResolve", "choco": "davinci-resolve",
        "flatpak": "com.blackmagicdesign.resolve",
        "brew_cask": "davinci-resolve", "category": "media",
        "download": "https://www.blackmagicdesign.com/products/davinciresolve",
    },

    # ── Graphics / Design ─────────────────────────────────────────────────────
    "gimp|gim|gimmp": {
        "canonical": "GIMP",
        "winget": "GIMP.GIMP", "choco": "gimp",
        "apt": "gimp", "snap": "gimp", "flatpak": "org.gimp.GIMP",
        "brew_cask": "gimp", "category": "design",
        "download": "https://www.gimp.org/downloads/",
    },
    "inkscape|inkscpae|inkscap": {
        "canonical": "Inkscape",
        "winget": "Inkscape.Inkscape", "choco": "inkscape",
        "apt": "inkscape", "flatpak": "org.inkscape.Inkscape",
        "brew_cask": "inkscape", "category": "design",
        "download": "https://inkscape.org/release/",
    },
    "blender|blnder|blendr": {
        "canonical": "Blender",
        "winget": "BlenderFoundation.Blender", "msstore": "9PP3C07GTVRH",
        "choco": "blender",
        "apt": "blender", "snap": "blender --classic",
        "flatpak": "org.blender.Blender",
        "brew_cask": "blender", "category": "design",
        "download": "https://www.blender.org/download/",
    },
    "krita|krita paint|krta": {
        "canonical": "Krita",
        "winget": "KDE.Krita", "msstore": "9N6X57ZGRXGX",
        "choco": "krita",
        "apt": "krita", "flatpak": "org.kde.krita",
        "brew_cask": "krita", "category": "design",
        "download": "https://krita.org/download/",
    },
    "figma|figmma|fgima": {
        "canonical": "Figma",
        "winget": "Figma.Figma", "choco": "figma",
        "flatpak": "io.github.Figma_Linux.figma_linux",
        "brew_cask": "figma", "category": "design",
        "download": "https://www.figma.com/downloads/",
    },

    # ── Games & Launchers ──────────────────────────────────────────────────────
    "steam|steeem|steem|stam": {
        "canonical": "Steam",
        "winget": "Valve.Steam", "choco": "steam", "scoop": "extras/steam",
        "apt": "steam", "flatpak": "com.valvesoftware.Steam",
        "brew_cask": "steam", "category": "game",
        "download": "https://store.steampowered.com/about/",
    },
    "epic games|epic games launcher|epicgames|epic|epik games": {
        "canonical": "Epic Games Launcher",
        "winget": "EpicGames.EpicGamesLauncher", "choco": "epicgameslauncher",
        "flatpak": "com.heroicgameslauncher.hgl",
        "brew_cask": "epic-games", "category": "game",
        "download": "https://store.epicgames.com/download",
    },
    "minecraft|mincraft|minecraf|minecrft|mineecraft": {
        "canonical": "Minecraft Launcher",
        "winget": "Mojang.MinecraftLauncher", "msstore": "9NBLGGH537BL",
        "choco": "minecraft-launcher",
        "flatpak": "com.mojang.Minecraft",
        "brew_cask": "minecraft", "category": "game",
        "download": "https://www.minecraft.net/download",
    },
    "fortnite|fortnit|fortnite game|fortnte": {
        "canonical": "Fortnite (via Epic Games Launcher)",
        "winget": "EpicGames.EpicGamesLauncher",
        "flatpak": "com.heroicgameslauncher.hgl",
        "brew_cask": "epic-games", "category": "game",
        "download": "https://store.epicgames.com/fortnite",
    },
    "gta5|gta 5|grand theft auto 5|grand theft auto v|gta v|gtav": {
        "canonical": "Grand Theft Auto V",
        "winget": "Rockstar.RockstarGamesLauncher",
        "is_uninstallable": True,
        "uninstallable_reason": "GTA V requires purchase. Buy and download via Steam: https://store.steampowered.com/app/271590 or Rockstar: https://www.rockstargames.com/gta-v",
        "category": "game",
        "download": "https://store.steampowered.com/app/271590",
    },
    "valorant|valaront|valrant": {
        "canonical": "Valorant",
        "winget": "RiotGames.Valorant.AP",
        "is_uninstallable": False,
        "category": "game",
        "download": "https://playvalorant.com/download/",
    },
    "league of legends|lol|leauge of legends": {
        "canonical": "League of Legends",
        "winget": "RiotGames.LeagueOfLegends.NA",
        "category": "game",
        "download": "https://signup.leagueoflegends.com/download",
    },
    "roblox|roblx|roblox game": {
        "canonical": "Roblox",
        "winget": "Roblox.Roblox", "msstore": "9NBLGGGZM6WM",
        "category": "game",
        "download": "https://www.roblox.com/download",
    },

    # ── Utilities ──────────────────────────────────────────────────────────────
    "7zip|7-zip|7 zip|sevn zip": {
        "canonical": "7-Zip",
        "winget": "7zip.7zip", "choco": "7zip", "scoop": "7zip",
        "apt": "p7zip-full", "dnf": "p7zip", "pacman": "p7zip",
        "brew_formula": "p7zip", "category": "utility",
        "download": "https://www.7-zip.org/download.html",
    },
    "winrar|win rar|winrr": {
        "canonical": "WinRAR",
        "winget": "RARLab.WinRAR", "choco": "winrar",
        "category": "utility", "download": "https://www.win-rar.com/download.html",
    },
    "notepad++|notepadpp|notepad plus plus": {
        "canonical": "Notepad++",
        "winget": "Notepad++.Notepad++", "choco": "notepadplusplus", "scoop": "notepadplusplus",
        "category": "utility", "download": "https://notepad-plus-plus.org/downloads/",
    },
    "notion|noton|nootion": {
        "canonical": "Notion",
        "winget": "Notion.Notion", "choco": "notion",
        "apt": "notion", "flatpak": "md.obsidian.Obsidian",
        "brew_cask": "notion", "mas": "1559269364",
        "category": "utility", "download": "https://www.notion.so/desktop",
    },
    "obsidian|obsidain|obisidian": {
        "canonical": "Obsidian",
        "winget": "Obsidian.Obsidian", "choco": "obsidian",
        "apt": "obsidian", "flatpak": "md.obsidian.Obsidian",
        "brew_cask": "obsidian", "mas": "1547905921",
        "category": "utility", "download": "https://obsidian.md/download",
    },
    "bitwarden|bitwrden|bitwardeen": {
        "canonical": "Bitwarden",
        "winget": "Bitwarden.Bitwarden", "msstore": "9PJSDV0VPK04",
        "choco": "bitwarden",
        "apt": "bitwarden", "snap": "bitwarden",
        "flatpak": "com.bitwarden.desktop",
        "brew_cask": "bitwarden", "mas": "1352778147",
        "category": "utility", "download": "https://bitwarden.com/download/",
    },
    "virtualbox|virtual box|virtalbox": {
        "canonical": "VirtualBox",
        "winget": "Oracle.VirtualBox", "choco": "virtualbox",
        "apt": "virtualbox", "dnf": "virtualbox",
        "brew_cask": "virtualbox", "category": "utility",
        "download": "https://www.virtualbox.org/wiki/Downloads",
    },
    "powertoys|power toys|powertoy": {
        "canonical": "Microsoft PowerToys",
        "winget": "Microsoft.PowerToys", "msstore": "XP89DCGQ3K6VLD",
        "choco": "powertoys", "scoop": "extras/powertoys",
        "category": "utility", "download": "https://github.com/microsoft/PowerToys/releases",
    },
    "rufus|rufues": {
        "canonical": "Rufus",
        "winget": "Rufus.Rufus", "choco": "rufus", "scoop": "extras/rufus",
        "category": "utility", "download": "https://rufus.ie/downloads/",
    },
    "etcher|balena etcher|etcher usb": {
        "canonical": "balenaEtcher",
        "winget": "Balena.Etcher", "choco": "etcher",
        "apt": "balena-etcher-electron",
        "brew_cask": "balenaetcher", "category": "utility",
        "download": "https://etcher.balena.io/#download-etcher",
    },
    "everything|everthing search|everything search": {
        "canonical": "Everything Search",
        "winget": "voidtools.Everything", "choco": "everything", "scoop": "everything",
        "category": "utility", "download": "https://www.voidtools.com/downloads/",
    },
    "libreoffice|libre office|liberoffice": {
        "canonical": "LibreOffice",
        "winget": "TheDocumentFoundation.LibreOffice", "choco": "libreoffice-fresh",
        "apt": "libreoffice", "snap": "libreoffice",
        "flatpak": "org.libreoffice.LibreOffice",
        "brew_cask": "libreoffice", "category": "utility",
        "download": "https://www.libreoffice.org/download/download-libreoffice/",
    },
    "thunderbird|thunder bird|thunderbrd": {
        "canonical": "Mozilla Thunderbird",
        "winget": "Mozilla.Thunderbird", "choco": "thunderbird",
        "apt": "thunderbird", "flatpak": "org.mozilla.Thunderbird",
        "brew_cask": "thunderbird", "mas": "1176895641",
        "category": "utility", "download": "https://www.thunderbird.net/download/",
    },
    "filezilla|file zilla|filzilla": {
        "canonical": "FileZilla",
        "winget": "TimKosse.FileZilla.Client", "choco": "filezilla",
        "apt": "filezilla", "flatpak": "org.filezillaproject.Filezilla",
        "brew_cask": "filezilla", "category": "utility",
        "download": "https://filezilla-project.org/download.php",
    },
    "putty|puty|ptty": {
        "canonical": "PuTTY",
        "winget": "PuTTY.PuTTY", "choco": "putty", "scoop": "putty",
        "apt": "putty", "brew_cask": "putty",
        "category": "utility", "download": "https://www.putty.org/",
    },

    # ── Python libraries ──────────────────────────────────────────────────────
    "numpy|numpay|numphy|numppy": {
        "canonical": "NumPy", "pip": "numpy",
        "is_python_package": True, "category": "python_library",
        "download": "https://pypi.org/project/numpy/",
    },
    "pandas|pndas|pandass": {
        "canonical": "Pandas", "pip": "pandas",
        "is_python_package": True, "category": "python_library",
        "download": "https://pypi.org/project/pandas/",
    },
    "matplotlib|matplot lib|matplotlb": {
        "canonical": "Matplotlib", "pip": "matplotlib",
        "is_python_package": True, "category": "python_library",
        "download": "https://pypi.org/project/matplotlib/",
    },
    "tensorflow|tensor flow|tensrflow": {
        "canonical": "TensorFlow", "pip": "tensorflow",
        "is_python_package": True, "category": "python_library",
        "download": "https://pypi.org/project/tensorflow/",
    },
    "pytorch|torch|py torch": {
        "canonical": "PyTorch", "pip": "torch",
        "is_python_package": True, "category": "python_library",
        "download": "https://pytorch.org/get-started/locally/",
    },
    "requests|request lib": {
        "canonical": "Requests", "pip": "requests",
        "is_python_package": True, "category": "python_library",
        "download": "https://pypi.org/project/requests/",
    },
    "flask|flusk|flsk": {
        "canonical": "Flask", "pip": "flask",
        "is_python_package": True, "category": "python_library",
        "download": "https://pypi.org/project/flask/",
    },
    "django|djnago|dajngo": {
        "canonical": "Django", "pip": "django",
        "is_python_package": True, "category": "python_library",
        "download": "https://pypi.org/project/django/",
    },
    "fastapi|fast api|fastapi framework": {
        "canonical": "FastAPI", "pip": "fastapi",
        "is_python_package": True, "category": "python_library",
        "download": "https://pypi.org/project/fastapi/",
    },
    "scikit learn|sklearn|scikit-learn|scikitlearn": {
        "canonical": "scikit-learn", "pip": "scikit-learn",
        "is_python_package": True, "category": "python_library",
        "download": "https://pypi.org/project/scikit-learn/",
    },
    "opencv|cv2|open cv": {
        "canonical": "OpenCV", "pip": "opencv-python",
        "is_python_package": True, "category": "python_library",
        "download": "https://pypi.org/project/opencv-python/",
    },

    # ── Truly uninstallable ───────────────────────────────────────────────────
    "photoshop|adobe photoshop|photoshoop|phoshop": {
        "canonical": "Adobe Photoshop",
        "is_uninstallable": True,
        "uninstallable_reason": "Adobe Photoshop requires a paid Adobe Creative Cloud subscription. Download from: https://www.adobe.com/products/photoshop.html",
        "download": "https://www.adobe.com/products/photoshop.html",
        "category": "design",
    },
    "premiere pro|adobe premiere|premire pro": {
        "canonical": "Adobe Premiere Pro",
        "is_uninstallable": True,
        "uninstallable_reason": "Requires paid Adobe Creative Cloud. Download from: https://www.adobe.com/products/premiere.html",
        "download": "https://www.adobe.com/products/premiere.html",
        "category": "media",
    },
    "after effects|aftereffects|after efects": {
        "canonical": "Adobe After Effects",
        "is_uninstallable": True,
        "uninstallable_reason": "Requires paid Adobe Creative Cloud. Download from: https://www.adobe.com/products/aftereffects.html",
        "download": "https://www.adobe.com/products/aftereffects.html",
        "category": "media",
    },
    "illustrator|adobe illustrator": {
        "canonical": "Adobe Illustrator",
        "is_uninstallable": True,
        "uninstallable_reason": "Requires paid Adobe Creative Cloud. Download from: https://www.adobe.com/products/illustrator.html",
        "download": "https://www.adobe.com/products/illustrator.html",
        "category": "design",
    },
    "xcode|x code": {
        "canonical": "Xcode",
        "is_uninstallable": True,
        "uninstallable_reason": "Xcode is macOS-only and must be installed from the Mac App Store: https://apps.apple.com/us/app/xcode/id497799835",
        "mas": "497799835",
        "download": "https://apps.apple.com/us/app/xcode/id497799835",
        "category": "dev_tool",
    },
    "ms office|microsoft office|office 365|word|excel|powerpoint": {
        "canonical": "Microsoft Office",
        "is_uninstallable": True,
        "uninstallable_reason": "Microsoft Office requires a Microsoft 365 subscription. Download from: https://www.microsoft.com/microsoft-365",
        "download": "https://www.microsoft.com/microsoft-365",
        "category": "utility",
    },
    "final cut pro|final cut": {
        "canonical": "Final Cut Pro",
        "is_uninstallable": True,
        "uninstallable_reason": "Final Cut Pro is macOS-only and paid. Get it from: https://www.apple.com/final-cut-pro/",
        "download": "https://www.apple.com/final-cut-pro/",
        "category": "media",
    },
}


# ── Flat alias → entry lookup (built at import time for O(1) lookup) ──────────

_ALIAS_MAP: dict[str, dict] = {}
for _aliases_str, _entry in APP_DB.items():
    for _alias in _aliases_str.split("|"):
        _ALIAS_MAP[_alias.strip().lower()] = _entry


# ── Fuzzy matcher ──────────────────────────────────────────────────────────────

def _local_resolve(raw: str) -> Optional[dict]:
    """
    Resolve app name against local DB.
    1. Exact match
    2. Fuzzy match (handles typos like discrod → discord)
    3. Partial / substring match
    """
    key = raw.strip().lower()

    # Exact
    if key in _ALIAS_MAP:
        return _ALIAS_MAP[key]

    # Fuzzy (cutoff=0.72 catches most reasonable typos)
    all_keys = list(_ALIAS_MAP.keys())
    matches = get_close_matches(key, all_keys, n=1, cutoff=0.72)
    if matches:
        log_step("✏️ ", f"Fuzzy matched '{raw}' → '{matches[0]}'")
        return _ALIAS_MAP[matches[0]]

    # Substring — user typed part of the name
    for alias, entry in _ALIAS_MAP.items():
        if key in alias or alias in key:
            log_step("✏️ ", f"Substring matched '{raw}' → '{alias}'")
            return entry

    return None


# ── LLM resolver (enhancement only — not critical path) ───────────────────────

def _llm_resolve(raw: str) -> Optional[dict]:
    """Ask LLM for package IDs. Returns None if LLM unavailable."""
    try:
        from llm_client import llm
        if not llm.is_groq_available():
            return None
    except Exception:
        return None

    prompt = f"""You are a software installation assistant.
The user wants to install: "{raw}"

Return ONLY valid JSON (no markdown) with these keys:
{{
  "canonical_name": "Full correct name",
  "winget_id": "winget package ID",
  "msstore_id": "MS Store product ID or empty",
  "choco_id": "chocolatey package name or empty",
  "scoop_id": "scoop package name or empty",
  "apt_id": "apt package name or empty",
  "snap_id": "snap package name (include --classic if needed) or empty",
  "flatpak_id": "flatpak app ID or empty",
  "dnf_id": "dnf package name or empty",
  "pacman_id": "pacman package name or empty",
  "brew_cask": "brew cask name or empty",
  "brew_formula": "brew formula name or empty",
  "mas_id": "Mac App Store numeric ID or empty",
  "npm_id": "npm global package name or empty",
  "pip_id": "PyPI package name or empty",
  "cargo_id": "Rust crate name or empty",
  "go_id": "Go module path or empty",
  "is_python_package": true or false,
  "is_uninstallable": true or false,
  "uninstallable_reason": "explanation with URL if uninstallable, else empty",
  "category": "browser|game|media|dev_tool|database|communication|utility|python_library|other",
  "direct_download_url": "official download page URL"
}}"""

    try:
        raw_resp = llm.chat(prompt, fast=False)
        raw_resp = raw_resp.strip()
        if "```json" in raw_resp:
            raw_resp = raw_resp.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_resp:
            raw_resp = raw_resp.split("```")[1].split("```")[0].strip()
        data = json.loads(raw_resp)
        canonical = data.get("canonical_name", raw)
        return {
            "canonical":    canonical,
            "winget":       data.get("winget_id", canonical),
            "msstore":      data.get("msstore_id", ""),
            "choco":        data.get("choco_id", ""),
            "scoop":        data.get("scoop_id", ""),
            "apt":          data.get("apt_id", ""),
            "snap":         data.get("snap_id", ""),
            "flatpak":      data.get("flatpak_id", ""),
            "dnf":          data.get("dnf_id", ""),
            "pacman":       data.get("pacman_id", ""),
            "brew_cask":    data.get("brew_cask", ""),
            "brew_formula": data.get("brew_formula", ""),
            "mas":          data.get("mas_id", ""),
            "npm":          data.get("npm_id", ""),
            "pip":          data.get("pip_id", ""),
            "cargo":        data.get("cargo_id", ""),
            "go":           data.get("go_id", ""),
            "is_python_package": data.get("is_python_package", False),
            "is_uninstallable":  data.get("is_uninstallable", False),
            "uninstallable_reason": data.get("uninstallable_reason", ""),
            "category":     data.get("category", "other"),
            "download":     data.get("direct_download_url", ""),
        }
    except Exception as exc:
        logger.warning(f"LLM resolve failed: {exc}")
        return None


# ── Live package manager search (last safety net) ─────────────────────────────

def _winget_search_best(query: str) -> Optional[str]:
    rc, out, _ = _run(
        ["winget", "search", query, "--accept-source-agreements", "--limit", "5"],
        timeout=30,
    )
    if rc != 0 or not out.strip():
        return None
    header_passed = False
    for line in out.splitlines():
        if re.match(r"^[-\s]+$", line):
            header_passed = True
            continue
        if header_passed and line.strip():
            parts = line.split()
            for part in parts:
                if "." in part and len(part) > 3:
                    return part
    return None


def _apt_search_best(query: str) -> Optional[str]:
    rc, out, _ = _run(["apt-cache", "search", "--names-only", query], timeout=15)
    if rc == 0 and out.strip():
        return out.splitlines()[0].split()[0]
    rc, out, _ = _run(["apt-cache", "search", query], timeout=15)
    if rc == 0 and out.strip():
        return out.splitlines()[0].split(" - ")[0].strip()
    return None


def _brew_search_best(query: str) -> Optional[tuple]:
    rc, out, _ = _run(["brew", "search", query], timeout=20)
    if rc != 0 or not out.strip():
        return None
    casks, formulae = [], []
    in_casks = False
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if "Casks" in line:
            in_casks = True
            continue
        if "Formulae" in line:
            in_casks = False
            continue
        (casks if in_casks else formulae).append(line)
    if casks:
        return casks[0], True
    if formulae:
        return formulae[0], False
    return None


# ── Subprocess runner ──────────────────────────────────────────────────────────

def _run(cmd: list, timeout: int = 300) -> tuple:
    print(f"    $ {' '.join(str(c) for c in cmd)}")
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        lines = []
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                print(f"    {line}")
                lines.append(line)
        proc.wait(timeout=timeout)
        return proc.returncode, "\n".join(lines), proc.stderr.read()
    except subprocess.TimeoutExpired:
        proc.kill()
        return -1, "", f"Timed out after {timeout}s"
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except Exception as exc:
        return -1, "", str(exc)


def _cmd_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None


# ── Result helpers ─────────────────────────────────────────────────────────────

@dataclass
class InstallResult:
    success: bool
    app_name: str
    resolved_name: str
    package_id: str
    method: str
    message: str
    already_installed: bool = False
    output: str = ""
    download_url: str = ""

    def to_dict(self) -> dict:
        return self.__dict__


def _ok(app_name, canonical, pkg_id, method, out=""):
    return InstallResult(True, app_name, canonical, pkg_id, method,
                         f"✅ Installed '{canonical}' via {method}.", output=out)

def _already(app_name, canonical):
    return InstallResult(True, app_name, canonical, canonical, "already_installed",
                         f"✅ '{canonical}' is already installed.", already_installed=True)

def _uninstallable(app_name, canonical, reason, url=""):
    return InstallResult(False, app_name, canonical, "", "none",
                         f"⚠️  '{canonical}' cannot be auto-installed.\n   {reason}",
                         download_url=url)

def _failure(app_name, canonical, tried, download_url=""):
    search = {
        "Windows": f"https://winstall.app/search?q={canonical.replace(' ', '+')}",
        "Linux":   f"https://repology.org/projects/?search={canonical.replace(' ', '+')}",
        "Darwin":  f"https://formulae.brew.sh/?q={canonical.replace(' ', '+')}",
    }
    url = download_url or search.get(OS, f"https://google.com/search?q=install+{canonical.replace(' ','+')} ")
    return InstallResult(False, app_name, canonical, "", "none",
        f"⚠️  Could not auto-install '{canonical}' (tried: {tried}).\n"
        f"   👉 Download manually: {url}",
        download_url=url)


# ── Already installed check ────────────────────────────────────────────────────

def is_installed(canonical: str, pip_id: str = "") -> bool:
    key = canonical.lower()
    binaries = {"git":"git","node":"node","nodejs":"node","python":"python3",
                "python 3":"python3","postgresql":"psql","mysql":"mysql",
                "redis":"redis-cli","docker":"docker","vim":"vim","neovim":"nvim",
                "curl":"curl","wget":"wget","ffmpeg":"ffmpeg","ollama":"ollama",
                "go":"go","rust":"rustc","ruby":"ruby","php":"php",
                "visual studio code":"code","gh":"gh","kubectl":"kubectl"}
    for k, b in binaries.items():
        if k in key and _cmd_exists(b):
            return True
    if pip_id:
        rc, _, _ = _run([sys.executable, "-m", "pip", "show", pip_id], timeout=10)
        if rc == 0:
            return True
    if OS == "Windows" and _cmd_exists("winget"):
        rc, out, _ = _run(
            ["winget", "list", "--name", canonical, "--accept-source-agreements"], timeout=30)
        if rc == 0 and canonical.lower() in out.lower():
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# OS INSTALL CHAINS
# ══════════════════════════════════════════════════════════════════════════════

def _install_windows(app_name: str, e: dict) -> InstallResult:
    canonical = e.get("canonical", app_name)
    pip_id    = e.get("pip", "")
    is_py     = e.get("is_python_package", False)

    if e.get("is_uninstallable"):
        return _uninstallable(app_name, canonical,
                              e.get("uninstallable_reason",""), e.get("download",""))

    if is_installed(canonical, pip_id):
        return _already(app_name, canonical)

    tried = []

    # pip (Python packages first)
    if is_py and pip_id:
        log_step("🐍", f"[pip] {pip_id}")
        rc, out, _ = _run([sys.executable, "-m", "pip", "install", pip_id])
        if rc == 0:
            return _ok(app_name, canonical, pip_id, "pip", out)
        tried.append("pip")

    # winget
    if _cmd_exists("winget"):
        pkg = e.get("winget", "") or _winget_search_best(canonical) or canonical
        log_step("📦", f"[winget] {pkg}")
        rc, out, err = _run(["winget","install","--id",pkg,"-e",
                              "--accept-package-agreements","--accept-source-agreements"])
        if rc == 0 or "already installed" in (out+err).lower():
            return _ok(app_name, canonical, pkg, "winget", out)
        # live search retry
        found = _winget_search_best(canonical)
        if found and found != pkg:
            log_step("🔍", f"[winget live] {found}")
            rc, out, _ = _run(["winget","install","--id",found,"-e",
                                "--accept-package-agreements","--accept-source-agreements"])
            if rc == 0:
                return _ok(app_name, canonical, found, "winget", out)
        tried.append("winget")

    # MS Store
    if _cmd_exists("winget"):
        store_id = e.get("msstore", "")
        if store_id:
            log_step("🏪", f"[MS Store] {store_id}")
            rc, out, _ = _run(["winget","install","--id",store_id,"--source","msstore",
                                "--accept-package-agreements","--accept-source-agreements"])
            if rc == 0:
                return _ok(app_name, canonical, store_id, "Microsoft Store", out)
        tried.append("msstore")

    # chocolatey
    if _cmd_exists("choco"):
        choco_id = e.get("choco","") or canonical.lower().replace(" ","-")
        log_step("🍫", f"[choco] {choco_id}")
        rc, out, _ = _run(["choco","install",choco_id,"-y"])
        if rc == 0:
            return _ok(app_name, canonical, choco_id, "chocolatey", out)
        tried.append("choco")

    # scoop
    if _cmd_exists("scoop"):
        scoop_id = e.get("scoop","") or canonical.lower().replace(" ","-")
        _run(["scoop","bucket","add","extras"], timeout=30)
        log_step("🥄", f"[scoop] {scoop_id}")
        rc, out, _ = _run(["scoop","install",scoop_id])
        if rc == 0:
            return _ok(app_name, canonical, scoop_id, "scoop", out)
        tried.append("scoop")

    # npm
    npm_id = e.get("npm","")
    if npm_id and _cmd_exists("npm"):
        log_step("📗", f"[npm] {npm_id}")
        rc, out, _ = _run(["npm","install","-g",npm_id])
        if rc == 0:
            return _ok(app_name, canonical, npm_id, "npm", out)
        tried.append("npm")

    # cargo
    cargo_id = e.get("cargo","")
    if cargo_id and _cmd_exists("cargo"):
        log_step("🦀", f"[cargo] {cargo_id}")
        rc, out, _ = _run(["cargo","install",cargo_id])
        if rc == 0:
            return _ok(app_name, canonical, cargo_id, "cargo", out)
        tried.append("cargo")

    # go install
    go_id = e.get("go","")
    if go_id and _cmd_exists("go"):
        go_pkg = go_id if "@" in go_id else f"{go_id}@latest"
        log_step("🐹", f"[go] {go_pkg}")
        rc, out, _ = _run(["go","install",go_pkg])
        if rc == 0:
            return _ok(app_name, canonical, go_pkg, "go install", out)
        tried.append("go")

    # pip fallback
    if pip_id and not is_py:
        rc, out, _ = _run([sys.executable,"-m","pip","install",pip_id])
        if rc == 0:
            return _ok(app_name, canonical, pip_id, "pip", out)
        tried.append("pip")

    return _failure(app_name, canonical, "/".join(tried), e.get("download",""))


def _install_linux(app_name: str, e: dict) -> InstallResult:
    canonical = e.get("canonical", app_name)
    pip_id    = e.get("pip", "")
    is_py     = e.get("is_python_package", False)

    if e.get("is_uninstallable"):
        return _uninstallable(app_name, canonical,
                              e.get("uninstallable_reason",""), e.get("download",""))
    if is_installed(canonical, pip_id):
        return _already(app_name, canonical)

    tried = []

    if is_py and pip_id:
        rc, out, _ = _run([sys.executable,"-m","pip","install",pip_id])
        if rc == 0:
            return _ok(app_name, canonical, pip_id, "pip", out)
        tried.append("pip")

    for apt_cmd in (["apt"], ["apt-get"]):
        if _cmd_exists(apt_cmd[0]):
            apt_id = e.get("apt","") or _apt_search_best(canonical) or canonical.lower().replace(" ","-")
            _run(["sudo", apt_cmd[0], "update", "-qq"], timeout=60)
            log_step("📦", f"[{apt_cmd[0]}] {apt_id}")
            rc, out, _ = _run(["sudo",apt_cmd[0],"install","-y",apt_id], timeout=300)
            if rc == 0:
                return _ok(app_name, canonical, apt_id, apt_cmd[0], out)
            tried.append(apt_cmd[0])
            break

    if _cmd_exists("snap"):
        snap_raw = e.get("snap","") or canonical.lower().replace(" ","-")
        parts = snap_raw.split()
        snap_id, flags = parts[0], parts[1:]
        for cmd in (["sudo","snap","install",snap_id]+flags,
                    ["sudo","snap","install",snap_id,"--classic"]):
            log_step("📦", f"[snap] {' '.join(cmd[3:])}")
            rc, out, _ = _run(cmd, timeout=300)
            if rc == 0:
                return _ok(app_name, canonical, snap_id, "snap", out)
        tried.append("snap")

    if _cmd_exists("flatpak"):
        fid = e.get("flatpak","") or canonical.lower().replace(" ",".")
        _run(["flatpak","remote-add","--if-not-exists","flathub",
              "https://flathub.org/repo/flathub.flatpakrepo"], timeout=30)
        log_step("📦", f"[flatpak] {fid}")
        rc, out, _ = _run(["flatpak","install","-y","flathub",fid], timeout=300)
        if rc == 0:
            return _ok(app_name, canonical, fid, "flatpak", out)
        tried.append("flatpak")

    for mgr in (["dnf"],["yum"]):
        if _cmd_exists(mgr[0]):
            did = e.get("dnf","") or canonical.lower().replace(" ","-")
            log_step("📦", f"[{mgr[0]}] {did}")
            rc, out, _ = _run(["sudo",mgr[0],"install","-y",did], timeout=300)
            if rc == 0:
                return _ok(app_name, canonical, did, mgr[0], out)
            tried.append(mgr[0])
            break

    for mgr in (["yay"],["pacman"]):
        if _cmd_exists(mgr[0]):
            pid = e.get("pacman","") or canonical.lower().replace(" ","-")
            cmd = ([mgr[0],"-S","--noconfirm",pid] if mgr[0]=="yay"
                   else ["sudo","pacman","-S","--noconfirm",pid])
            log_step("📦", f"[{mgr[0]}] {pid}")
            rc, out, _ = _run(cmd, timeout=300)
            if rc == 0:
                return _ok(app_name, canonical, pid, mgr[0], out)
            tried.append(mgr[0])
            break

    if _cmd_exists("zypper"):
        zid = canonical.lower().replace(" ","-")
        log_step("📦", f"[zypper] {zid}")
        rc, out, _ = _run(["sudo","zypper","install","-y",zid], timeout=300)
        if rc == 0:
            return _ok(app_name, canonical, zid, "zypper", out)
        tried.append("zypper")

    npm_id = e.get("npm","")
    if npm_id and _cmd_exists("npm"):
        rc, out, _ = _run(["npm","install","-g",npm_id])
        if rc == 0:
            return _ok(app_name, canonical, npm_id, "npm", out)
        tried.append("npm")

    cargo_id = e.get("cargo","")
    if cargo_id and _cmd_exists("cargo"):
        rc, out, _ = _run(["cargo","install",cargo_id])
        if rc == 0:
            return _ok(app_name, canonical, cargo_id, "cargo", out)
        tried.append("cargo")

    go_id = e.get("go","")
    if go_id and _cmd_exists("go"):
        go_pkg = go_id if "@" in go_id else f"{go_id}@latest"
        rc, out, _ = _run(["go","install",go_pkg])
        if rc == 0:
            return _ok(app_name, canonical, go_pkg, "go install", out)
        tried.append("go")

    if pip_id and not is_py:
        rc, out, _ = _run([sys.executable,"-m","pip","install",pip_id])
        if rc == 0:
            return _ok(app_name, canonical, pip_id, "pip", out)
        tried.append("pip")

    return _failure(app_name, canonical, "/".join(tried), e.get("download",""))


def _install_macos(app_name: str, e: dict) -> InstallResult:
    canonical = e.get("canonical", app_name)
    pip_id    = e.get("pip", "")
    is_py     = e.get("is_python_package", False)

    if e.get("is_uninstallable"):
        # Special case: Xcode has a mas ID
        if e.get("mas") and _cmd_exists("mas"):
            log_step("🍎", f"[mas] {e['mas']}")
            rc, out, _ = _run(["mas","install", e["mas"]])
            if rc == 0:
                return _ok(app_name, canonical, e["mas"], "Mac App Store", out)
        return _uninstallable(app_name, canonical,
                              e.get("uninstallable_reason",""), e.get("download",""))
    if is_installed(canonical, pip_id):
        return _already(app_name, canonical)

    tried = []

    if is_py and pip_id:
        rc, out, _ = _run([sys.executable,"-m","pip","install",pip_id])
        if rc == 0:
            return _ok(app_name, canonical, pip_id, "pip", out)
        tried.append("pip")

    if _cmd_exists("brew"):
        cask = e.get("brew_cask","")
        if not cask:
            res = _brew_search_best(canonical)
            if res:
                cask = res[0]
        if cask:
            log_step("🍺", f"[brew cask] {cask}")
            rc, out, _ = _run(["brew","install","--cask",cask], timeout=300)
            if rc == 0:
                return _ok(app_name, canonical, cask, "brew cask", out)
        tried.append("brew cask")

        formula = e.get("brew_formula","")
        if not formula:
            res = _brew_search_best(canonical)
            if res:
                formula = res[0]
        if formula:
            log_step("🍺", f"[brew formula] {formula}")
            rc, out, _ = _run(["brew","install",formula], timeout=300)
            if rc == 0:
                return _ok(app_name, canonical, formula, "brew formula", out)
        tried.append("brew formula")

    if _cmd_exists("mas"):
        mas_id = e.get("mas","")
        if mas_id:
            log_step("🍎", f"[mas] {mas_id}")
            rc, out, _ = _run(["mas","install",mas_id])
            if rc == 0:
                return _ok(app_name, canonical, mas_id, "Mac App Store", out)
        tried.append("mas")

    npm_id = e.get("npm","")
    if npm_id and _cmd_exists("npm"):
        rc, out, _ = _run(["npm","install","-g",npm_id])
        if rc == 0:
            return _ok(app_name, canonical, npm_id, "npm", out)
        tried.append("npm")

    cargo_id = e.get("cargo","")
    if cargo_id and _cmd_exists("cargo"):
        rc, out, _ = _run(["cargo","install",cargo_id])
        if rc == 0:
            return _ok(app_name, canonical, cargo_id, "cargo", out)
        tried.append("cargo")

    go_id = e.get("go","")
    if go_id and _cmd_exists("go"):
        go_pkg = go_id if "@" in go_id else f"{go_id}@latest"
        rc, out, _ = _run(["go","install",go_pkg])
        if rc == 0:
            return _ok(app_name, canonical, go_pkg, "go install", out)
        tried.append("go")

    if pip_id and not is_py:
        rc, out, _ = _run([sys.executable,"-m","pip","install",pip_id])
        if rc == 0:
            return _ok(app_name, canonical, pip_id, "pip", out)
        tried.append("pip")

    return _failure(app_name, canonical, "/".join(tried), e.get("download",""))


# ── Public API ─────────────────────────────────────────────────────────────────

def install_software(app_name: str) -> dict:
    """
    Install any software — handles typos, slang, abbreviations.
    Works fully offline (local DB + fuzzy match).
    If LLM available, used as enhancement for unknown apps.
    """
    print(f"\n  📦 Resolving: '{app_name}'  (OS: {OS})")

    # 1. Local DB + fuzzy match (always works, no LLM needed)
    entry = _local_resolve(app_name)

    if entry:
        canonical = entry.get("canonical", app_name)
        log_step("✅", f"Matched in local DB: '{app_name}' → '{canonical}'")
    else:
        # 2. LLM enhancement (if available)
        log_step("🧠", f"Not in local DB — trying LLM resolution…")
        llm_entry = _llm_resolve(app_name)
        if llm_entry:
            entry = llm_entry
            canonical = entry.get("canonical", app_name)
            log_step("✅", f"LLM resolved: '{app_name}' → '{canonical}'")
        else:
            # 3. Last resort — use raw name with live search
            log_step("🔍", f"Falling back to live package manager search…")
            slug = app_name.lower().replace(" ", "-")
            entry = {
                "canonical": app_name,
                "winget": app_name, "choco": slug, "scoop": slug,
                "apt": slug, "snap": slug, "flatpak": "",
                "brew_cask": slug, "brew_formula": slug,
                "pip": slug, "is_python_package": False,
                "is_uninstallable": False, "download": "",
            }

    if OS == "Windows":
        result = _install_windows(app_name, entry)
    elif OS == "Linux":
        result = _install_linux(app_name, entry)
    elif OS == "Darwin":
        result = _install_macos(app_name, entry)
    else:
        result = InstallResult(False, app_name, entry.get("canonical", app_name),
                               "", "none", f"❌ Unsupported OS: {OS}",
                               download_url=entry.get("download",""))

    print(f"\n  {'✅' if result.success else '⚠️ '} {result.message}")
    return result.to_dict()


def search_package(app_name: str) -> list[str]:
    results: list[str] = []
    if OS == "Windows" and _cmd_exists("winget"):
        rc, out, _ = _run(
            ["winget","search",app_name,"--accept-source-agreements"], timeout=30)
        if rc == 0:
            header_passed = False
            for line in out.splitlines():
                if re.match(r"^[-\s]+$", line):
                    header_passed = True
                    continue
                if header_passed and line.strip():
                    parts = line.split()
                    if len(parts) >= 2:
                        results.append(parts[1])
    elif OS == "Linux" and _cmd_exists("apt-cache"):
        rc, out, _ = _run(["apt-cache","search",app_name], timeout=20)
        if rc == 0:
            for line in out.splitlines():
                results.append(line.split(" - ")[0].strip())
    elif OS == "Darwin" and _cmd_exists("brew"):
        rc, out, _ = _run(["brew","search",app_name], timeout=20)
        if rc == 0:
            results = [l.strip() for l in out.splitlines()
                       if l.strip() and not l.startswith("=")]
    return results[:10]
