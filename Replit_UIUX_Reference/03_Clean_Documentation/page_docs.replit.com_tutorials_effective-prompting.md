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
Vibe Coding & Prompting
Efficient prompting with Replit AI
##### Vibe Coding & Prompting
* [Efficient prompting](/tutorials/effective-prompting)
* [Vibe Code Effectively](/tutorials/how-to-vibe-code)
* [Vibe coding 101](/tutorials/vibe-coding-101)
##### Agent
* [Canvas vs. App Mode](/tutorials/design-vs-build-mode)
* [Plan vs. Build Mode](/tutorials/plan-vs-build-mode)
* [Agent Skills](/tutorials/agent-skills)
* [Notion-powered website](/tutorials/build-a-notion-powered-website)
* [Launch a mobile app](/tutorials/build-and-launch-a-mobile-app)
* [Troubleshooting](/tutorials/mobile-app-troubleshooting)
* [Create mobile apps with Replit](/tutorials/expo-on-replit)
* [Claude Agent SDK](/tutorials/claude-agent-sdk)
##### Security
* [Built-in security features](/tutorials/vibe-code-securely)
* [Security checklist](/tutorials/vibe-code-security-checklist)
##### MCP
* [Learn about MCP in 3 minutes](/tutorials/mcp-in-3)
##### Data storage
* [Share a Database](/tutorials/share-database-across-apps)
* [Add a SQL database](/getting-started/quickstarts/database-connection)
* [App Storage in Python](/getting-started/quickstarts/object-storage-python)
* [App Storage in JavaScript](/getting-started/quickstarts/object-storage-javascript)
> ## Documentation Index
>
> Fetch the complete documentation index at: <https://docs.replit.com/llms.txt>
>
> Use this file to discover all available pages before exploring further.
Effective prompting is about giving clear instructions to a capable assistant. Guide [**Agent**](/core-concepts/agent) well, and you’ll go from idea to app fast.
## [​](#quick-examples) Quick examples
See the difference between vague and effective prompts:
Fixing code
**Vague:** “Fix my code.”**Effective:** “My script fails when processing user input. The error seems to be in the validation function. Can you help debug the `validate_input` part? Here’s the error message: [details]”The effective prompt identifies the problem area, suspected function, and provides context.
Building features
**Vague:** “Make a website.”**Effective:** “Create a simple portfolio website with sections for Home, About Me, and Contact Form. Use a clean, modern design theme and placeholder content.”The effective prompt defines the purpose, core features, and desired aesthetic.
Performance improvements
**Vague:** “Don’t make it slow.”**Effective:** “Refactor the data processing function to handle larger inputs more efficiently. Could we use a different algorithm or data structure?”Tell Agent *how* to improve rather than using negative constraints.
UI changes
**Vague:** “Add animation.”**Effective:** “Animate the main image on the landing page so it gently fades in when the page first loads to create a welcoming effect.”Identify the specific element, desired effect, timing, and intended experience.
Complex systems
**Vague:** “Build the backend.”**Effective:** “Set up the server-side logic. Implement user authentication (signup/login) and create an API endpoint to retrieve user profile data securely.”Break large tasks into specific functionalities.
## [​](#core-principles) Core principles
![Ten tips for effective prompting with Replit Agent](https://mintcdn.com/replit/0ixNWaRF232g0Gwn/images/tutorials/10-tips.png?fit=max&auto=format&n=0ixNWaRF232g0Gwn&q=85&s=21e1e7269802faa767fef0187bdb6e3d)
### [​](#plan-first) Plan first
Before prompting, outline your app’s features and user flows. A clear plan leads to more focused prompts.
Think through your application’s structure like a product manager would. Break the overall goal into logical stages.
**Instead of:** “Build a task manager app.”
**Try:** “1. Create the basic HTML structure with an input field and task list. 2. Add JavaScript to add tasks. 3. Use a database to store tasks. 4. Add functionality to mark tasks complete.”
Then prompt Agent for each step.
### [​](#build-incrementally) Build incrementally
Use [**Checkpoints**](/core-concepts/agent#checkpoints) to save progress after each successful step. If something breaks, you can roll back to a working state and try a different approach.
**Instead of:** “Build a complete e-commerce platform.”
**Try:** “Set up a basic full-stack project for an e-commerce site with user sign-up and login using Replit Auth.” Then follow with prompts for product listings, cart, and checkout.
### [​](#be-specific) Be specific
Define exactly what you need: output formats, constraints, edge cases.
**Instead of:** “Add a contact form.”
**Try:** “Create a contact form page at `/contact` with fields for:
* Name (required)
* Email (required, must be valid format)
* Message (required, min 10 characters)
On submit, send the form data to `contact@mydomain.com`.”
### [​](#use-positive-language) Use positive language
State what you *want*, not what to avoid.
**Instead of:** “Don’t make the user profile page confusing.”
**Try:** “Design a clean user profile page. Display the username prominently, followed by email and join date. Include an ‘Edit Profile’ button.”
### [​](#keep-it-simple) Keep it simple
Use clear, straightforward language. Break complex requests into bullet points.
**Instead of:** “Implement the necessary server-side infrastructure to facilitate the dynamic generation and retrieval of user-generated content artifacts.”
**Try:** “Create backend functionality for users to submit blog posts. Users should enter a title and body content. Store posts in the database.”
## [​](#working-with-context) Working with context
### [​](#provide-relevant-files) Provide relevant files
Mention specific files rather than attaching your entire project.
**Instead of:** (Attaching everything) “Implement the user profile page based on our design system.”
**Try:** “Create the user profile page. Fetch user data from the server endpoint. Style according to [URL to design docs] and match this mockup: [attach `profile_mockup.jpg`].”
Start a new chat when switching to unrelated tasks. This prevents confusion from accumulated context.
### [​](#show-examples) Show examples
Reduce ambiguity by providing concrete examples—code snippets, sample data, or screenshots.
**Instead of:** “Make the product cards look better.”
**Try:** “Redesign the product cards on the shop page. Each card should display the product image, name, price, and an ‘Add to Cart’ button, similar to this layout: [attach screenshot]. Use a light gray border.”
## [​](#debugging-effectively) Debugging effectively
When errors occur, provide:
* The **exact** error message
* Relevant code snippets
* File names where the error occurs
* What you were trying to achieve
* Steps you’ve already tried
**Instead of:** “My login page is broken.”
**Try:** “When I log in with correct credentials on `/login`, I get a ‘User not found’ error in the browser console. The database check doesn’t seem to work. Here’s the login handling code in `auth.js`.”
## [​](#ask-for-guidance) Ask for guidance
Switch to Plan mode to explore options before building. Ask Agent about libraries, approaches, and trade-offs.
**Instead of:** “Add payments.”
**Try:** “What are some good options for accepting credit card payments in a web app built on Replit? I need something relatively simple to integrate.”
## [​](#iterate-on-your-prompts) Iterate on your prompts
Your first prompt might not be perfect—that’s normal. If the result isn’t right:
* Add more detail
* Provide an example
* Simplify the instruction
* Try a different way of explaining
**Initial:** “Create a header for my website.”
**Refined:** “Create a sticky header component with the site logo on the left and navigation links (Home, About, Contact) on the right.”
## [​](#summary) Summary
Effective prompting comes down to:
* **Planning** before you prompt
* **Building** incrementally with Checkpoints
* **Being specific** about requirements
* **Providing context** through examples and relevant files
* **Iterating** when results aren’t quite right
Master these principles and you’ll build apps faster with Agent.
Was this page helpful?
YesNo
[How to vibe code effectively
Next](/tutorials/how-to-vibe-code)
Ctrl+I
On this page
* [Quick examples](#quick-examples)
* [Core principles](#core-principles)
* [Plan first](#plan-first)
* [Build incrementally](#build-incrementally)
* [Be specific](#be-specific)
* [Use positive language](#use-positive-language)
* [Keep it simple](#keep-it-simple)
* [Working with context](#working-with-context)
* [Provide relevant files](#provide-relevant-files)
* [Show examples](#show-examples)
* [Debugging effectively](#debugging-effectively)
* [Ask for guidance](#ask-for-guidance)
* [Iterate on your prompts](#iterate-on-your-prompts)
* [Summary](#summary)