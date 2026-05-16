[Skip to main content](#content-area)
[Replit home page![light logo](https://mintcdn.com/replit/j4of5To8jzehz0JK/logo/light.svg?fit=max&auto=format&n=j4of5To8jzehz0JK&q=85&s=6209cb5927b56fd1781133af05428373)![dark logo](https://mintcdn.com/replit/j4of5To8jzehz0JK/logo/dark.svg?fit=max&auto=format&n=j4of5To8jzehz0JK&q=85&s=9cbba158300d48c49d8b888e71015495)](/getting-started/intro-replit)
![US](https://d3gk2c5xim1je2.cloudfront.net/flags/US.svg)
English
Search...
Ctrl K
* [Start Building](https://replit.com?ref=docs)
[Replit home page![light logo](https://mintcdn.com/replit/j4of5To8jzehz0JK/logo/light.svg?fit=max&auto=format&n=j4of5To8jzehz0JK&q=85&s=6209cb5927b56fd1781133af05428373)![dark logo](https://mintcdn.com/replit/j4of5To8jzehz0JK/logo/dark.svg?fit=max&auto=format&n=j4of5To8jzehz0JK&q=85&s=9cbba158300d48c49d8b888e71015495)](/getting-started/intro-replit)
Search or ask...
Navigation
Additional Resources
Streaming native graphics using VNC
##### Getting Started
* [Overview](/getting-started/intro-replit)
* Quickstarts
* [Build in ChatGPT](/getting-started/quickstarts/build-in-chatgpt)
* Import
##### Core Concepts
* [How Replit works](/core-concepts/how-replit-works)
* [Overview](/category/replit-apps)
* [Overview](/category/cloud-services)
* Agent
* Workspaces
* Project Editor
* Security
* Design
* Projects
* Storage
* Integrations
* Monetization
##### Platforms
* [Mobile App](/platforms/mobile-app)
* [ChatGPT](/platforms/chatgpt)
##### Additional Resources
* [CLUI](/additional-resources/clui-graphical-cli)
* [Cheat Sheet](/additional-resources/cheat-sheet)
* [Shared responsibility model](/additional-resources/shared-responsibility-model)
* [Google Authentication in Python and Flask](/additional-resources/google-auth-in-flask)
* [Streaming native graphics using VNC](/additional-resources/streaming-native-graphics-vnc)
* [FAQ](/faq)
> ## Documentation Index
>
> Fetch the complete documentation index at: <https://docs.replit.com/llms.txt>
>
> Use this file to discover all available pages before exploring further.
This streaming technology allows you to work with legacy applications in your browser from any device! For example, you could run a Python-powered game designed for desktop right on your mobile phone or tablet without making any changes to the underlying code.
[Tetris (powered by PyGame)](https://replit.com/@demcrepl/Tetris-in-Pygame)
![image of Tetris in a Replit App](https://mintcdn.com/replit/0ixNWaRF232g0Gwn/images/vnc/tetris.png?fit=max&auto=format&n=0ixNWaRF232g0Gwn&q=85&s=ddf138f52d698a6baa7532ad74e88492)
## [​](#how-can-i-use-vnc) How Can I Use VNC?
Any Replit App – in any language – can use a virtual desktop. No changes are needed to execute native graphics programs on Replit. The VNC pane will appear when any application attempts to open a native desktop window.
## [​](#securing-your-replit-app) Securing Your Replit App
By default, your VNC connection does not have a password and can only be accessed from <https://replit.com> since the connection relies on the same authentication used for the WebSocket. If you need to access your Replit App via the external [noVNC](https://novnc.com) client, you can set a VNC password.
Set a password in your Replit App [secrets](/core-concepts/project-editor/app-setup/secrets) configuration. `Secrets` is a secure place to store passwords without the fear of other users accessing your passwords. Setting `VNC_PASSWORD` will add enhanced security when connecting remotely.
## [​](#how-can-i-use-fullscreen-vnc) How Can I Use Fullscreen VNC?
You must have secured your Replit App as instructed above to proceed with these steps.
1. Execute the following command in your “Shell” tab:
   ```
   echo $REPL_ID
   ```
![image showing the echo command](https://mintcdn.com/replit/0ixNWaRF232g0Gwn/images/vnc/replid.png?fit=max&auto=format&n=0ixNWaRF232g0Gwn&q=85&s=45fcde093041183e77ba334b780e69ed)
2. Construct your connection URL by replacing `REPL_ID` in with the output from above: `<\REPL_ID\>.id.repl.co`
3. Open the [noVNC client](https://novnc.com/noVNC/vnc.html) in a separate browser tab.
4. Open connection settings.
![open connection settings](https://mintcdn.com/replit/0ixNWaRF232g0Gwn/images/vnc/settings.png?fit=max&auto=format&n=0ixNWaRF232g0Gwn&q=85&s=1f46fa987102bf1bb95ea027d6b3f43d)
5. Expand the WebSockets field. Enter your connection URL (`\<REPL_ID\>.id.repl.co`) in the `host` field, and leave the `path` field empty.
![host](https://mintcdn.com/replit/0ixNWaRF232g0Gwn/images/vnc/host.png?fit=max&auto=format&n=0ixNWaRF232g0Gwn&q=85&s=e1a6420a7573bb6f0e7a327ecd89b60d)
6. Change the `Scaling Mode` to `Remote Resizing`:
![scaling](https://mintcdn.com/replit/0ixNWaRF232g0Gwn/images/vnc/scaling.png?fit=max&auto=format&n=0ixNWaRF232g0Gwn&q=85&s=cc7bcd3ec3d8c5cf801d537eef6a2b16)
7. Use the `runner` username and the password configured above when asked for credentials.
## [​](#examples) Examples
* [PyGame](https://replit.com/@demcrepl/Tetris-in-Pygame)
* [Python matplotlib](https://replit.com/@amasad-matplotlib)
* [Java Processing](https://replit.com/@sigcse2021/Game-of-Life-demcrepl)
Was this page helpful?
YesNo
[Google Authentication in Python and Flask
Previous](/additional-resources/google-auth-in-flask)[FAQ
Next](/faq)
Ctrl+I
On this page
* [How Can I Use VNC?](#how-can-i-use-vnc)
* [Securing Your Replit App](#securing-your-replit-app)
* [How Can I Use Fullscreen VNC?](#how-can-i-use-fullscreen-vnc)
* [Examples](#examples)