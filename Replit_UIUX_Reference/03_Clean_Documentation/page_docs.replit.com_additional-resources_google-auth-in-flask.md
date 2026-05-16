[Skip to main content](#content-area)
[Replit home page![light logo](https://mintcdn.com/replit/j4of5To8jzehz0JK/logo/light.svg?fit=max&auto=format&n=j4of5To8jzehz0JK&q=85&s=6209cb5927b56fd1781133af05428373)![dark logo](https://mintcdn.com/replit/j4of5To8jzehz0JK/logo/dark.svg?fit=max&auto=format&n=j4of5To8jzehz0JK&q=85&s=9cbba158300d48c49d8b888e71015495)](/getting-started/intro-replit)
![US](https://d3gk2c5xim1je2.cloudfront.net/flags/US.svg)
English
Search...
⌘K
* [Start Building](https://replit.com?ref=docs)
[Replit home page![light logo](https://mintcdn.com/replit/j4of5To8jzehz0JK/logo/light.svg?fit=max&auto=format&n=j4of5To8jzehz0JK&q=85&s=6209cb5927b56fd1781133af05428373)![dark logo](https://mintcdn.com/replit/j4of5To8jzehz0JK/logo/dark.svg?fit=max&auto=format&n=j4of5To8jzehz0JK&q=85&s=9cbba158300d48c49d8b888e71015495)](/getting-started/intro-replit)
Search or ask...
Navigation
Additional Resources
Google Authentication in Python and Flask
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
Allowing your users to log in to your website using their Google account has these benefits:
1. You don’t have to implement your own authentication scheme.
2. You can get users’ name and contact information easily.
3. You can use the same credentials to access users’ Google resources like Sheets and Drive.
This guide that will walk you through how to do that with Python and Flask on Replit.
First, we’ll walk through how to setup basic OAuth authentication, then cover how to use the resulting credentials to access users’ Google resources.
## [​](#introduction-to-oauth) Introduction to OAuth
Google authentication is based on the OAuth standard. The way OAuth works is as follows:
1. Somewhere on your website, you direct a user to a login page.
2. When they go to the login page, you don’t implement the login form on your website, but instead redirect to Google’s login service to login the user.
3. When Google’s login service successfully logs in the user, it redirects back to your website at a predefined URL of your choosing, say `https://YOUR_DOMAIN/oauth2callback`, while sending some information pertinent to the user and the login session.
4. You use the user’s login information to further obtain an access token, which is like a pass you can use to access the user’s resources, like their profile information, their spreadsheets, documents and more.
## [​](#oauth-show-me-the-code) OAuth: Show me the code
If you are like me, the first thing you want is working code. The code below is what you need. However, you’ll need to set up some things in your Google Cloud Console in order to get everything working. That will be covered in the next section. Create a new Replit App using the [Flask template](https://replit.com/@replit/Flask?v=1) and put the following in `main.py`. The comments in the code explains what the individual parts do:
```
from flask import Flask, redirect, session, url_for, request
import google_auth_oauthlib.flow
import json
import os
import requests
app = Flask('app')
# `FLASK_SECRET_KEY` is used by sessions. You should create a random string
# and store it as secret.
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or os.urandom(24)
# `GOOGLE_APIS_OAUTH_SECRET` contains the contents of a JSON file to be downloaded
# from the Google Cloud Credentials panel. See next section.
oauth_config = json.loads(os.environ['GOOGLE_OAUTH_SECRETS'])
# This sets up a configuration for the OAuth flow
oauth_flow = google_auth_oauthlib.flow.Flow.from_client_config(
    oauth_config,
    # scopes define what APIs you want to access on behave of the user once authenticated
    scopes=[
        "https://www.googleapis.com/auth/userinfo.email",
        "openid",
        "https://www.googleapis.com/auth/userinfo.profile",
    ]
)
# This is entrypoint of the login page. It will redirect to the Google login service located at the
# `authorization_url`. The `redirect_uri` is actually the URI which the Google login service will use to
# redirect back to this app.
@app.route('/signin')
def signin():
    # We rewrite the URL from http to https because inside the Replit App http is used,
    # but externally it's accessed via https, and the redirect_uri has to match that
    oauth_flow.redirect_uri = url_for('oauth2callback', _external=True).replace('http://', 'https://')
    authorization_url, state = oauth_flow.authorization_url()
    session['state'] = state
    return redirect(authorization_url)
# This is the endpoint that Google login service redirects back to. It must be added to the "Authorized redirect URIs"
# in the API credentials panel within Google Cloud. It will call a Google endpoint to request
# an access token and store it in the user session. After this, the access token can be used to access
# APIs on behalf of the user.
@app.route('/oauth2callback')
def oauth2callback():
    if not session['state'] == request.args['state']:
        return 'Invalid state parameter', 400
    oauth_flow.fetch_token(authorization_response=request.url.replace('http:', 'https:'))
    session['access_token'] = oauth_flow.credentials.token
    return redirect("/")
# This is the home page of the app. It directs the user to log in if they are not already.
# It shows the user info's information if they already are.
@app.route('/')
def welcome():
    if "access_token" in session:
        user_info = get_user_info(session["access_token"])
        if user_info:
            return f"""
                Hello {user_info["given_name"]}!<br>
                Your email address is {user_info["email"]}<br>
                <a href="/logout">Log out</a>
            """
    return """
        <h1>Hello!</h1>
        <a href="/signin">Sign In via Google</a><br>
    """
# Call the userinfo API to get the user's information with a valid access token.
# This is the first example of using the access token to access an API on the user's behalf.
def get_user_info(access_token):
    response = requests.get("https://www.googleapis.com/oauth2/v3/userinfo", headers={
       "Authorization": f"Bearer {access_token}"
   })
    if response.status_code == 200:
        user_info = response.json()
        return user_info
    else:
        print(f"Failed to fetch user info: {response.status_code} {response.text}")
        return None
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
```
## [​](#set-up-your-oauth-app-/-client) Set up your OAuth App / Client
To get the above code working, you’ll need to do these things in Google Cloud.
1. Create a Google Cloud project (if you don’t already have one).
2. Configure the OAuth consent screen.
3. Create an OAuth client ID for your app.
### [​](#create-a-google-cloud-project) Create a Google Cloud project
If you already have a Google Cloud project you want to use for this exercise, you can skip this step.
1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Click on the project selector dropbox next to the Google Cloud logo:
![Google Project Dropdown](https://mintcdn.com/replit/rBzGsKp9NcWJ7sib/images/google-auth-in-flask/google-project-dropdown.png?fit=max&auto=format&n=rBzGsKp9NcWJ7sib&q=85&s=a30f634d9a23d770a6d2f4011d0d9dae)
3. Select an existing project or Click “New Project” and create an new project.
![Select Project](https://mintcdn.com/replit/rBzGsKp9NcWJ7sib/images/google-auth-in-flask/select-project.png?fit=max&auto=format&n=rBzGsKp9NcWJ7sib&q=85&s=e74b58a12409487b6185d0c26768467c)
4. If creating a new project, enter a project name, and click “Create”.
![New Project](https://mintcdn.com/replit/rBzGsKp9NcWJ7sib/images/google-auth-in-flask/new-project.png?fit=max&auto=format&n=rBzGsKp9NcWJ7sib&q=85&s=4435873966a96504d766496022464e75)
If you see your new project show up in a popup, click “Select project” to make that the *active project*.
### [​](#configure-the-oauth-consent-screen) Configure the OAuth consent screen
Now that you have a project, you can configure the OAuth consent screen for it:
1. Go to the [OAuth Consent Screen](https://console.cloud.google.com/apis/credentials/consent)
![OAuth Consent Screen](https://mintcdn.com/replit/rBzGsKp9NcWJ7sib/images/google-auth-in-flask/oauth-consent-screen.png?fit=max&auto=format&n=rBzGsKp9NcWJ7sib&q=85&s=49c9930fd4dcec9194a31221fb0dcdfa)
2. Make sure the project in the project drop down is the one you want.
3. Select “External” to allow any user to log in to your app with a Google account. “Internal” will allow only people from your organization.
4. Click “Create”.
5. Enter an app name and the email of the person supporting this app (you?)
![OAuth Consent Screen](https://mintcdn.com/replit/rBzGsKp9NcWJ7sib/images/google-auth-in-flask/oauth-consent-step-2.png?fit=max&auto=format&n=rBzGsKp9NcWJ7sib&q=85&s=613070383292b3a679e1f9653036f660)
6. Enter an email address under “Developer contact information”.
![Developer contact information](https://mintcdn.com/replit/rBzGsKp9NcWJ7sib/images/google-auth-in-flask/oauth-consent-step-2-b.png?fit=max&auto=format&n=rBzGsKp9NcWJ7sib&q=85&s=82001a9c28a09f1ba232f2c6b80b3118)
7. Click “Save and continue”.
8. In the Scopes screen, you can add the APIs you want your app to have access to. You already have access to the APIs for getting basic user information.
![OAuth Scopes](https://mintcdn.com/replit/rBzGsKp9NcWJ7sib/images/google-auth-in-flask/oauth-scopes.png?fit=max&auto=format&n=rBzGsKp9NcWJ7sib&q=85&s=616e631af44331268912e1b054ed2d4d)
For now, leave this as is and click “Save and continue”.
9. In Test Users, you need to add the email of the users you want to be able to test the app during its testing phase.
![Test Users](https://mintcdn.com/replit/rBzGsKp9NcWJ7sib/images/google-auth-in-flask/test-users.png?fit=max&auto=format&n=rBzGsKp9NcWJ7sib&q=85&s=2d3d6dd451cf47b3c5d49a6fe1362b2d)
Click “Add users”
10. Add one or more email Google email addresses, and click “Add”.
![Test Users](https://mintcdn.com/replit/rBzGsKp9NcWJ7sib/images/google-auth-in-flask/test-users-add.png?fit=max&auto=format&n=rBzGsKp9NcWJ7sib&q=85&s=d2b34047e7e9d45c5933f1abe3e29dcd)
Then click “Save and continue”.
11. Review the summary screen. You can always go back and edit any of the steps.
![Test Users](https://mintcdn.com/replit/rBzGsKp9NcWJ7sib/images/google-auth-in-flask/summary.png?fit=max&auto=format&n=rBzGsKp9NcWJ7sib&q=85&s=37a3ea92af93f314a105d4eb35b4cd14)
### [​](#create-an-oauth-client-id-for-your-app) Create an OAuth client ID for your app
This is the last part. To get OAuth working, you need to create an OAuth client ID for the app.
1. Go to [Credentials](https://console.cloud.google.com/apis/credentials).
![Test Users](https://mintcdn.com/replit/rBzGsKp9NcWJ7sib/images/google-auth-in-flask/credentials.png?fit=max&auto=format&n=rBzGsKp9NcWJ7sib&q=85&s=456919dc3158da1e521882c6dbebec9d)
2. Click “Create credentials”
![Test Users](https://mintcdn.com/replit/rBzGsKp9NcWJ7sib/images/google-auth-in-flask/create-credentials.png?fit=max&auto=format&n=rBzGsKp9NcWJ7sib&q=85&s=1d7d149734afd522a6b54787771b8614)
select “OAuth client ID”.
3. Select “Web application” for Application type. Enter a name for this client ID.
![Test Users](https://mintcdn.com/replit/rBzGsKp9NcWJ7sib/images/google-auth-in-flask/create-credentials-web.png?fit=max&auto=format&n=rBzGsKp9NcWJ7sib&q=85&s=27d176683f267e10e559e91969936ec9)
4. Now, go to your Flask Replit App. Open the shell, and enter: `echo https://$REPLIT_DEV_DOMAIN/oauth2callback`. The result will look something like: `https://81309e9b-c4df-48e0-a2c2-0a8d3c0e3162-00-35ppsa0tcuv6v.infra-staging.replit.dev/oauth2callback`. Copy this text and enter it as one of the “Authorized redirect URIs” in the bottom of the form
![Test Users](https://mintcdn.com/replit/rBzGsKp9NcWJ7sib/images/google-auth-in-flask/authorized-redirect-uris.png?fit=max&auto=format&n=rBzGsKp9NcWJ7sib&q=85&s=0cbf4b9efec37c1d0d63241d1453eead)
Later when you publish your app, you’ll want to come back here to add another entry `https://YOUR_APP_DOMAIN/oauth2callback`
5. Click “Create”
6. Click “Download JSON”:
![Test Users](https://mintcdn.com/replit/rBzGsKp9NcWJ7sib/images/google-auth-in-flask/secrets_json.png?fit=max&auto=format&n=rBzGsKp9NcWJ7sib&q=85&s=b51a15e9157b0949d7fc6a1e69930acf)
7. Go to your Replit App again, open the Secrets pane. Create a secret named `GOOGLE_OAUTH_SECRETS`, and paste of the contents of the downloaded file
   as the secret value.
![Test Users](https://mintcdn.com/replit/rBzGsKp9NcWJ7sib/images/google-auth-in-flask/add-secret.png?fit=max&auto=format&n=rBzGsKp9NcWJ7sib&q=85&s=bd181325e743c64e07eb0f84e7d9ee05)
Phew! That was tedious. Congratulations if you made it through! Now you can run the Flask app and log in using a test user Google account. To make your app available to any Google user, you’ll need to go back to the [consent page](https://console.cloud.google.com/apis/credentials/consent) and click “Publish App”. A verification process may be required if your app requires additional Google APIs like Sheets and Drive.
Next, we’ll cover how to integrate with a Google API like Sheets. Follow along if you want to go further.
## [​](#google-sheets-api-setup) Google Sheets API Setup
In order to add a Google API integration like Google Sheets, first you need to enable the API for the app. You can [browser the available APIs](https://console.cloud.google.com/apis/library). As an example, we’ll use Google Sheets.
1. Go to the [Google Sheets API listing page](https://console.cloud.google.com/apis/library/sheets.googleapis.com).
2. Click “Enable”.
Done! That’s all the Google Cloud setup you had to do for this part.
## [​](#google-sheets-integration-show-me-the-code) Google Sheets Integration: Show me the code
First, in the oauth flow section of the original code, we leave everything the same, except add `"https://www.googleapis.com/auth/spreadsheets.readonly"` to the list of scopes:
```
# This sets up a configuration for the OAuth flow
oauth_flow = google_auth_oauthlib.flow.Flow.from_client_config(
    oauth_config,
    # scopes define what APIs you want to access on behave of the user once authenticated
    scopes=[
        "https://www.googleapis.com/auth/userinfo.email",
        "openid",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/spreadsheets.readonly"
    ]
)
```
Now, the way you access a Google API with the `googleapiclient.discovery` library is to first create a `Credentials` object using the access token, and then use the `build` function to create a callable API object. For the sheets API it looks like:
```
credentials = google.oauth2.credentials.Credentials(token=session['access_token'])
service = build("sheets", "v4", credentials=credentials)
sheets_api = service.spreadsheets()
```
As to how to actually use the Sheets API, I’ve created a couple of helper functions:
```
# fetch all sheets within a Google spreadsheet
def get_sheets(sheets_api, spreadsheet_id) -> list[str]:
    result = sheets_api.get(spreadsheetId=spreadsheet_id).execute()
    return [sheet["properties"]["title"] for sheet in result["sheets"]]
# fetch the data for a given sheet within a Google spreadsheet
def get_sheet_data(sheets_api, spreadsheet_id, sheet_title) -> list[list[str]]:
    result = (
        sheets_api.values()
        .get(spreadsheetId=spreadsheet_id, range=sheet_title)
        .execute()
    )
    return result["values"]
```
With the above help, we can create a POST handler endpoint that imports a Google spreadsheet like so:
```
@app.route("/import_spreadsheet", methods = ['POST'])
def import_spreadsheet():
    if 'access_token' not in session:
        return redirect('/signin')
    spreadsheet_id = request.form["spreadsheet_id"]
    credentials = google.oauth2.credentials.Credentials(token=session['access_token'])
    service = build("sheets", "v4", credentials=credentials)
    sheets_api = service.spreadsheets()
    try:
        sheets = get_sheets(sheets_api, spreadsheet_id)
        data_by_sheets = {}
        for sheet in sheets:
            data = get_sheet_data(sheets_api, spreadsheet_id, sheet)
            data_by_sheets[sheet] = data
    except googleapiclient.errors.HttpError as e:
        return f"upload failure"
    dirpath = os.path.join("static", "uploads", spreadsheet_id)
    filepath = os.path.join(dirpath, "data.json")
    os.makedirs(dirpath, exist_ok=True)
    with open(filepath, "w") as file:
        json.dump(data_by_sheets, file)
    return "upload success!"
```
Here is the full working code:
```
from flask import Flask, redirect, session, url_for, request
import google_auth_oauthlib.flow
import json
import os
import requests
from googleapiclient.discovery import build
import googleapiclient.errors
import google.oauth2.credentials
app = Flask('app')
# `FLASK_SECRET_KEY` is used by sessions. You should create a random string
# and store it as secret.
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or os.urandom(24)
# `GOOGLE_APIS_OAUTH_SECRET` contains the contents of a JSON file to be downloaded
# from the Google Cloud Credentials panel. See next section.
oauth_config = json.loads(os.environ['GOOGLE_OAUTH_SECRETS'])
# This sets up a configuration for the OAuth flow
oauth_flow = google_auth_oauthlib.flow.Flow.from_client_config(
    oauth_config,
    # scopes define what APIs you want to access on behave of the user once authenticated
    scopes=[
        "https://www.googleapis.com/auth/userinfo.email",
        "openid",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/spreadsheets.readonly"
    ]
)
# This is entrypoint of the login page. It will redirect to the Google login service located at the
# `authorization_url`. The `redirect_uri` is actually the URI which the Google login service will use to
# redirect back to this app.
@app.route('/signin')
def signin():
    # We rewrite the URL from http to https because inside the Replit App http is used,
    # but externally it's accessed via https, and the redirect_uri has to match that
    oauth_flow.redirect_uri = url_for('oauth2callback', _external=True).replace('http://', 'https://')
    authorization_url, state = oauth_flow.authorization_url()
    session['state'] = state
    return redirect(authorization_url)
# This is the endpoint that Google login service redirects back to. It must be added to the "Authorized redirect URIs"
# in the API credentials panel within Google Cloud. It will call a Google endpoint to request
# an access token and store it in the user session. After this, the access token can be used to access
# APIs on behalf of the user.
@app.route('/oauth2callback')
def oauth2callback():
    if not session['state'] == request.args['state']:
        return 'Invalid state parameter', 400
    oauth_flow.fetch_token(authorization_response=request.url.replace('http:', 'https:'))
    session['access_token'] = oauth_flow.credentials.token
    return redirect("/")
# Call the userinfo API to get the user's information with a valid access token.
# This is the first example of using the access token to access an API on the user's behalf.
def get_user_info(access_token):
    response = requests.get("https://www.googleapis.com/oauth2/v3/userinfo", headers={
       "Authorization": f"Bearer {access_token}"
   })
    if response.status_code == 200:
        user_info = response.json()
        return user_info
    else:
        print(f"Failed to fetch user info: {response.status_code} {response.text}")
        return None
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')
# fetch all sheets within a Google spreadsheet
def get_sheets(sheets_api, spreadsheet_id) -> list[str]:
    result = sheets_api.get(spreadsheetId=spreadsheet_id).execute()
    return [sheet["properties"]["title"] for sheet in result["sheets"]]
# fetch the data for a given sheet within a Google spreadsheet
def get_sheet_data(sheets_api, spreadsheet_id, sheet_title) -> list[list[str]]:
    result = (
        sheets_api.values()
        .get(spreadsheetId=spreadsheet_id, range=sheet_title)
        .execute()
    )
    return result["values"]
# Render a form to allow importing a spreadsheet
@app.route("/import_spreadsheet_form")
def import_spreadsheet_form():
    return """
    <h3>Import Spreadsheet</h3>
    <form action="/import_spreadsheet" method="POST">
        <label>Spreadsheet ID</label>
        <input type="text" name="spreadsheet_id">
        <button type="submit">Import</button>
    </form>
    """
@app.route("/import_spreadsheet", methods = ['POST'])
def import_spreadsheet():
    if 'access_token' not in session:
        return redirect('/signin')
    spreadsheet_id = request.form["spreadsheet_id"]
    credentials = google.oauth2.credentials.Credentials(token=session['access_token'])
    service = build("sheets", "v4", credentials=credentials)
    sheets_api = service.spreadsheets()
    try:
        sheets = get_sheets(sheets_api, spreadsheet_id)
        data_by_sheets = {}
        for sheet in sheets:
            data = get_sheet_data(sheets_api, spreadsheet_id, sheet)
            data_by_sheets[sheet] = data
    except googleapiclient.errors.HttpError as e:
        return f"upload failure"
    dirpath = os.path.join("static", "uploads", spreadsheet_id)
    filepath = os.path.join(dirpath, "data.json")
    os.makedirs(dirpath, exist_ok=True)
    with open(filepath, "w") as file:
        json.dump(data_by_sheets, file)
    return "upload success! Really!"
@app.route('/')
def welcome():
    if "access_token" in session:
        user_info = get_user_info(session["access_token"])
        if user_info:
            return f"""
            Hello {user_info["given_name"]}!<br>
            Your email address is {user_info["email"]}<br>
            <a href="/signin">Sign In to Google</a><br>
            <a href="/import_spreadsheet_form">Import a Sheet</a>
            """
    return """
    <h1>Welcome to Google Sheet Importer</h1>
    <a href="/signin">Sign In to Google</a><br>
    <a href="/import_spreadsheet_form">Import a Sheet</a>
    """
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
```
Remember, if you publish the app. Make sure to:
1. Added the production `/oauth2callback` URI for to the “Authorized redirect URIs”.
2. Go to the [consent page](https://console.cloud.google.com/apis/credentials/consent) and “Publish App”.
Hope you had a good experience, and hope you Enjoy your further advantures.
Was this page helpful?
YesNo
[Replit shared responsibility model
Previous](/additional-resources/shared-responsibility-model)[Streaming native graphics using VNC
Next](/additional-resources/streaming-native-graphics-vnc)
⌘I
On this page
* [Introduction to OAuth](#introduction-to-oauth)
* [OAuth: Show me the code](#oauth-show-me-the-code)
* [Set up your OAuth App / Client](#set-up-your-oauth-app-%2F-client)
* [Create a Google Cloud project](#create-a-google-cloud-project)
* [Configure the OAuth consent screen](#configure-the-oauth-consent-screen)
* [Create an OAuth client ID for your app](#create-an-oauth-client-id-for-your-app)
* [Google Sheets API Setup](#google-sheets-api-setup)
* [Google Sheets Integration: Show me the code](#google-sheets-integration-show-me-the-code)