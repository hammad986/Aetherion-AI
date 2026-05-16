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
Platforms
Replit in ChatGPT
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
The Replit integration in ChatGPT lets you create, update, and inspect Replit Apps directly from your conversations—no setup required. Build real apps by talking to ChatGPT and let Agent handle the rest.
## [​](#what-is-replit-in-chatgpt) What is Replit in ChatGPT
Replit in ChatGPT connects your ChatGPT conversations to Replit’s powerful development platform. You can describe what you want to build, and Agent will create a fully functional Replit App for you. Once created, you can ask ChatGPT to modify, update, or analyze your app—all through natural conversation.
## [​](#getting-started) Getting started
Follow these steps to connect Replit to ChatGPT and create your first app.
### [​](#connect-replit-to-chatgpt) Connect Replit to ChatGPT
You can connect Replit to ChatGPT in two ways:
Connect from Settings
1. Click your profile icon in ChatGPT
2. Go to **Settings** → **Apps & Connectors**
3. Find **Replit** and click **Connect**
4. Complete the OAuth authorization with your Replit account
Connect from a conversation
1. Start a new conversation in ChatGPT
2. Invoke Replit using one of the methods below
3. ChatGPT will guide you through the OAuth flow if you’re not connected
### [​](#invoke-replit-in-chatgpt) Invoke Replit in ChatGPT
Once connected, you can invoke Replit in three ways:
| Method | How to use |
| --- | --- |
| Direct mention | Type `Replit, <request>` at the start of your message |
| Slash command | Type `/Replit` in the composer |
| Composer menu | Click the **+** button → **More** → **Replit** |
### [​](#create-your-first-app) Create your first app
After connecting, create your first Replit App:
1. Start a new conversation in ChatGPT
2. Invoke Replit and describe what you want to build (e.g., “Replit, create a todo app with dark mode”)
3. Agent will generate your app and deploy it automatically
4. Once complete, you’ll receive a link to view your app
Each conversation can work with one Replit App. If you create a new app in the same conversation, it will modify the existing app. To create multiple apps, start separate conversations.
## [​](#features) Features
* **[Replit AI](/core-concepts/how-replit-works)**: Agent generates and deploys apps based on your natural language requests
* **[Project Editor](/core-concepts/project-editor)**: Full-featured development environment accessible from ChatGPT
* **[Publishing](/category/replit-deployments)**: Apps are automatically deployed and ready to share
What you can do
**Create:** Build new Replit Apps by describing what you want. Agent generates the application and deploys it automatically.**Update:** Modify the app in your current conversation. Ask ChatGPT to add features, fix bugs, or make changes, and Agent will apply them.**Inspect:** Analyze your app without making changes. Get information about code structure, file paths, or behavior.
Supported file formats
You can include various file types in your prompts:
| Format | Supported |
| --- | --- |
| PDF | Yes |
| CSV | Yes |
| Common document formats | Yes |
| Images (raw files) | No (use public URLs instead) |
| Videos (raw files) | No (use public URLs instead) |
To include images or videos in your app, provide public URLs in your prompt. You can also add media files directly in Replit after the app is created.
Build times
Agent typically builds apps in:
| App complexity | Typical build time |
| --- | --- |
| Simple (calculators, small landing pages) | 2–3 minutes |
| Complex (games, rich animations, dense dashboards) | 3–4 minutes |
## [​](#availability-and-plans) Availability and plans
ChatGPT plans
The Replit integration works with all ChatGPT plan types:
| Plan | Supported | Notes |
| --- | --- | --- |
| Free | Yes |  |
| Plus | Yes |  |
| Pro | Yes |  |
| Business | Yes | Requires admin enablement in **Workspace settings → Connectors** |
| Enterprise/Edu | Yes | Requires admin enablement in **Workspace settings → Connectors** |
**Regional availability:** All regions outside the EU.
Replit plans
All Replit plan types support the ChatGPT integration:
| Plan | Supported | Notes |
| --- | --- | --- |
| Starter (Free) | Yes | Public apps only |
| Core | Yes | Private apps, extended builds, Publishing, Database |
| Pro | Yes | Higher monthly credit allowance, all Core features |
| Teams | Yes | Private apps, extended builds, publishing, databases |
| Enterprise | Yes | Full feature access |
## [​](#billing-and-credits) Billing and credits
Work done by Replit in ChatGPT is billed as Replit Agent usage in your Replit account.
How billing works
* **Core** and **Pro** plans include monthly credits that cover Agent, publishing, and databases up to your allowance
* You can buy **credit packs** that expire after six months and do not auto‑renew — ideal for launch spikes or experiments
* Use **usage limits** and **budgets** in Replit’s billing settings to cap spend
Learn more in the [AI billing documentation](/billing/ai-billing).
## [​](#current-limitations) Current limitations
Currently, the Replit app in ChatGPT:
* Can only work on the app created in that specific conversation
* Cannot open or modify:
  + Apps created in other ChatGPT threads
  + Apps you built directly in Replit
Feature parity with the full Replit experience (databases, secrets management, more advanced Project Editor controls) is coming later.
## [​](#troubleshooting) Troubleshooting
Replit not appearing in Browse apps
If you don’t see Replit in **Browse apps**, check:
* Whether apps are available in your region
* Whether your workspace admin has enabled the Replit app (Business/Enterprise/Edu)
Build failures
If builds fail, open the app in Replit:
* Check usage and billing
* Use checkpoints and rollbacks to revert
* Ask Agent for help directly in the Project Editor
## [​](#next-steps) Next steps
* **[Build in ChatGPT quickstart](/getting-started/quickstarts/build-in-chatgpt)**: Create your first app in 5 minutes
* **[Replit AI](/core-concepts/how-replit-works)**: Learn more about Agent and AI-powered tools
* **[Project Editor](/core-concepts/project-editor)**: Explore the full development environment
* **[Publishing](/category/replit-deployments)**: Share and deploy your apps
* **[Support](https://replit.com/~?supportform=true)**: Contact us for help with Replit in ChatGPT
Was this page helpful?
YesNo
[Replit Mobile App
Previous](/platforms/mobile-app)[The Graphical Command Line Interface
Next](/additional-resources/clui-graphical-cli)
Ctrl+I
On this page
* [What is Replit in ChatGPT](#what-is-replit-in-chatgpt)
* [Getting started](#getting-started)
* [Connect Replit to ChatGPT](#connect-replit-to-chatgpt)
* [Invoke Replit in ChatGPT](#invoke-replit-in-chatgpt)
* [Create your first app](#create-your-first-app)
* [Features](#features)
* [Availability and plans](#availability-and-plans)
* [Billing and credits](#billing-and-credits)
* [Current limitations](#current-limitations)
* [Troubleshooting](#troubleshooting)
* [Next steps](#next-steps)