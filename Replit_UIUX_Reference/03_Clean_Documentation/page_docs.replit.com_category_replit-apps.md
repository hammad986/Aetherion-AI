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
Core Concepts
Replit Apps
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
## [​](#what-is-a-replit-app) What is a Replit App?
Replit Apps are cloud-hosted projects that contain code, data, and assets.
You can create, run, and publish them from a secure, isolated environment.
Replit Apps integrate with the following tools in the Project Editor to provide
a seamless development experience:
* **AI-powered tools**: Use Agent to create, debug, and explain your code.
* **Collaboration**: Work with others in real time on the same app.
* **Publishing**: Publish your app to the cloud with a single click.
* **Templates**: Start your app quickly using preset configurations for various use cases.
## [​](#getting-started) Getting started
For step-by-step instructions on creating Replit Apps, see the following Quickstart guides:
* [Remix an App](getting-started/quickstarts/remix-an-app)
* [Create with AI](/getting-started/quickstarts/ask-ai)
* [Build from Scratch](/tutorials/effective-prompting)
To open a Replit App, log into Replit and open it in the Project Editor using one
of the following methods.
Create a new Replit App
1. Select ![plus icon](https://mintcdn.com/replit/rJldsgYVucXB_6kW/images/icons/create-app-icon.svg?fit=max&auto=format&n=rJldsgYVucXB_6kW&q=85&s=cc16ebed380bbd324dafaa2c93a91a66) **Create App**.
   You should see the following screen:
   ![Create a new App tabs](https://mintcdn.com/replit/AZ1L8RlIroSxuJDa/images/replit-apps/create-new-app.png?fit=max&auto=format&n=AZ1L8RlIroSxuJDa&q=85&s=2352edcc0ba788cbc369edc1844e23f9)
2. Select one of the following options:
   * **Create with Replit Agent**: Use AI-powered tools to create a new Replit App.
   * **Choose a Template**: Create a new Replit App based on an existing one.
   * **Import from GitHub**: Create a new Replit App from a GitHub repository.
3. Complete the dialog prompts to start a new Replit App.
Open an existing Replit App
To access a Replit App you created previously, select ![folder icon](https://mintcdn.com/replit/rJldsgYVucXB_6kW/images/icons/folder-icon.svg?fit=max&auto=format&n=rJldsgYVucXB_6kW&q=85&s=d862b8c10221031dcf14121a7bce4c5a) **Apps** from the left sidebar.
## [​](#key-features) Key features
Replit Apps offer the following features:
* **Zero-setup:** Create apps or write code directly on Replit.com without any installs or configuration.
* **Auto-save:** Your project continuously saves changes to the cloud and lets you resume coding from any web browser
* **Version Control:** Track changes, explore file history, and sync your files without any configuration, through Replit’s version control systems
* **Public/Private Visibility Controls:** Control who can view, run, or create a Remix of your app with privacy settings
* **Publishing:** Publish your code to the cloud without making any complex configuration changes
* **Custom App URLs:** Get a unique URL for your app or assign a custom domain for a professional presence
Files uploaded to your project file system are only available during development and
aren’t accessible to your published app or other builders. Use [Object
Storage](/cloud-services/storage-and-databases/object-storage) to handle
builder uploads and serve files and [Replit
Database](/cloud-services/storage-and-databases/sql-database) to store and
retrieve data for your app and users.
To learn more about Project Editor tools that streamline Replit App creation, see [Project Editor Overview](/core-concepts/project-editor).
## [​](#how-it-works) How it works
When you create a Replit App, Replit sets up a private space for your project in the cloud.
As you add features and modifications to your app, Replit saves your changes
automatically so you can resume editing from any web browser.
Replit provides pre-configured environments with all the necessary components.
This lets you start creating your app immediately without worrying about
server configuration, database setup, or environment management.
Replit automatically assigns each Replit App a unique web address where you
can preview your app while you’re working on it. When you’re ready to share your
creation, you can publish it with just a few clicks to make it available 24/7.
## [​](#storage-overview) Storage Overview
Replit offers four primary types of storage to meet your application’s data needs. Each storage type serves different use cases and has specific limits based on your plan.
Storage limits include all data stored by your app, including installed
packages and dependencies.
| Storage Type | Description | Use Cases | Persistence | Plan Limits |
| --- | --- | --- | --- | --- |
| **File Storage** | Files in your project | Application code, static assets, configuration files | Persisted on publishing, resets on restart | Starter: 2GB Core: 50GB Teams: 256GB Enterprise: Custom |
| **[Database](/cloud-services/storage-and-databases/sql-database)** | Structured data storage | User profiles, game scores, product catalogs | Fully persistent across sessions | 20GB per development database Production databases are billed by compute time and storage |
| **[App Storage](/cloud-services/storage-and-databases/object-storage)** | Unstructured data and media | Images, videos, PDFs, documents | Fully persistent across sessions | Pay-per-use model Billed by storage and bandwidth |
| **[Secrets](/core-concepts/project-editor/app-setup/secrets)** | Encrypted sensitive data | API keys, credentials, connection strings | Fully persistent and encrypted | No specific limits Included with all plans |
For detailed pricing and usage-based billing information, see the [Billing
documentation](/category/billing) and [Storage and Databases
overview](/category/storage-and-databases).
## [​](#use-cases) Use cases
The following examples showcase how you can use Replit Apps to accelerate your
app creation process.
### [​](#explore-something-new) Explore something new
Select a template to start coding in a specific programming language or software stack.
![screenshot of a template description](https://mintcdn.com/replit/AZ1L8RlIroSxuJDa/images/replit-apps/template-use-case.png?fit=max&auto=format&n=AZ1L8RlIroSxuJDa&q=85&s=87e1b6bc29fe9b188e84a67a14cf66eb)
### [​](#create-and-test-apis) Create and test APIs
Build an API with RESTful endpoints and use Project Editor tools to test them before going live.
![screenshot of code from an API and the API Request Tester](https://mintcdn.com/replit/AZ1L8RlIroSxuJDa/images/replit-apps/api-use-case.png?fit=max&auto=format&n=AZ1L8RlIroSxuJDa&q=85&s=a2cfcb002e1a4716ca5cfa7214ae88dd)
## [​](#next-steps) Next steps
To learn more about Replit Apps, see the following resources:
* [Templates](https://www.replit.com/templates/): explore starter project setups to give you a head start
* [Publishing](/category/replit-deployments): learn which publishing option works best for your Replit App
* [Custom Domains](cloud-services/deployments/custom-domains#custom-domains-with-published-apps): Set your domain to link to your Replit App
* [Storage and Databases](/category/storage-and-databases): Discover your storage options
Was this page helpful?
YesNo
[How Replit works
Previous](/core-concepts/how-replit-works)[Cloud Services
Next](/category/cloud-services)
Ctrl+I
On this page
* [What is a Replit App?](#what-is-a-replit-app)
* [Getting started](#getting-started)
* [Key features](#key-features)
* [How it works](#how-it-works)
* [Storage Overview](#storage-overview)
* [Use cases](#use-cases)
* [Explore something new](#explore-something-new)
* [Create and test APIs](#create-and-test-apis)
* [Next steps](#next-steps)