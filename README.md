# Thrive

A multi-purpose messaging an social client (Mastodon for now)
Thrive is a messaging and social media application that allows you to chat with others and keep up with various social and chat platforms. To start with, Mastodon is the only supported platform as this is in very early development.
Thrive is designed to be 100% accessible for those using screen readers such as [NVDA](https://nvaccess.org/about-nvda/) and [JAWS](https://www.freedomscientific.com/products/software/jaws/). It achieves this through a graphical user interface (GUI) that is both easy to navigate with a keyboard and on-screen elements that are clearly labelled for a screen reader. Sound effects for various user actions and program events are also planned.

## Disclaimer

As of now, Thrive is in the alpha stage of development. This means that features will be extremely basic and you might experience significant bugs. Run this software at your own risk.

## Thrive features

For now, the Thrive suite only features a basic Mastodon client which will allow you to post to the [Mastodon](https://fedi.tips/what-is-mastodon-what-is-the-fediverse/) platform and interact with others' posts.

## Running Thrive

### from source

The Thrive suite is written in Python, so you will need to [Download and install Python](https://www.python.org/downloads/). Installing Python itself is outside the scope of this documentation.
These instructions apply to the Windows operating system, so you will also need [Git for Windows](https://gitforwindows.org/) installed.

1. Press Windows + R, type cmd, and press Enter to launch the command prompt.
2. Clone this repository with git by running the following command.
```
git clone https://github.com/G4p-Studios/Thrive.git
```
3. Create a virtual Python environment. This will allow you to install libraries and run Thrive inside an isolated workspace without touching your main Python install.
```
cd Thrive
python -m venv venv
```

4. Activate the virtual environment.
```
venv\scripts\activate
```
5. Ensure that pip, setuptools and wheel are up to date to avoid errors installing libraries.
```
python -m pip install --upgrade pip setuptools wheel
```
6. Install the libraries needed for Thrive.
```
pip install -r requirements.txt
```
7. Run Thrive.
```
cd mastodon
python thrive.py
```
### Compiled.
If you just want a pre-compiled binary without having to fight with Python, you can [download the latest release here](https://github.com/G4p-Studios/Thrive/releases/download/v0.0.4.0-alpha4/thrive.zip). To run this, simply extract the zip file and run thrive.exe.
The pre-compiled Thrive binary runs on Windows 7 and higher.

## Authorising to Mastodon

When you launch Thrive for the first time, you will be greeted with a Mastodon authorisation screen.

1. Enter the URL of the Mastodon instance you have an account on, E.G. https://mastodon.social. You don't need the https://part, just the domain (E.G. mastodon.social) will do.
2. Tab to the username field and enter your Mastodon username without the @ sign or instance domain. For instance, if your username is user@mastodon.social, you'd just enter user.
3. Tab to the authenticate button and hit Space or Enter.
4. Your default web browser will open to your instance's login/authorisation page. If you are not logged into your account, enter your login details and click login. Once logged in, click the authorise button.
5. Click the copy button to copy the authorisation code shown on screen.
6. Close the browser, Alt Tab to the enter code dialog, paste in your authorisation code with Control V and hit Enter.

Your user data is stored inside a file called user.dat inside the Thrive folder. If you ever need to reauthorise, simply delete that file and repeat the above steps.

## Posting to Mastodon

Once Thrive is authorised, you will land on a textbox where you can write a Mastodon post. You can optionally add a content warning if your post is very long or contains content that might upset people. Once you check the content warning box, Shift Tab once to access the content warning title field and enter your warning. Once done, tab to the post button and press Enter or Space.

## Viewing and interacting with posts.

Shift once from the post textbox and you'll see a list of posts from users on your home timeline. Use the up and down arrow keys to navigate this list. In this initial view, you can see the user, the post message, and the number of replies, boosts and favourites the post has.
Pressing Enter on a post will bring up a dialog where you can find various details about the post, such as who posted it, what client they used, when it was posted, replies, boosts and favourites. Tab around this dialog and you'll also see options to reply to, boost and favourite the post. Press Escape to close this dialog.
Note: you will only see the user's client if the post was sent from the same instance as the one you're on. Otherwise, it will show as unknown. This is because client data doesn't federate across instances.

## Sound packs

### What are sound packs?

Sound packs are collections of sounds that Thrive uses to indicate user actions and program events. In terms of structure, a sound pack is essentially a folder with wave files inside of it.

### Creating and adding sound packs

You can use the default sound pack as a basis when creating your own sound packs. To add a sound pack, create a folder inside Thrive's sounds folder called mastodon-packname, replacing packname with your desired sound pack name, then add your wave files into the folder. Be sure to check your filenames against the default folder, otherwise your sounds might not play.

### Changing sound packs.

you can change sound packs by doing the following:

1. Press Alt + S to open the settings dialog.
2. Choose the sound pack you want from the dropdown menu, tab to the save button and press either Space or Enter.
3. Shut down Thrive with Alt F4.
4. Restart  Thrive again and enjoy your new sounds!

Note: The folder structure for sound packs is the same as the [TweeseCake](https://tweesecake.app) program, so if you have a TweeseCake sound pack you wish to port over to Thrive, no converting or renaming of files is needed.

## Credits

* Alex Chapman: initial product idea and development.
* Hamid, A.K.A. Kalahami: Bug fixes, code improvements and feature additions.
* Mason Armstrong and Mckensie Parker: Alpha testers.
* Stuart Hughes, A.K.A. TwoThousandStu: pip requirements, compile script, readme file and initial alpha release binary.