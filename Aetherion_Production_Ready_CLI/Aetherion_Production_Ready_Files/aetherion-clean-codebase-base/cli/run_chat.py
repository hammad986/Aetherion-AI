import requests, os
resp = requests.post('http://127.0.0.1:5000/api/auth/login', json={'email':'test4@example.com', 'password':'password123'}).json()
token = resp['access_token']
os.environ['AETHERION_TOKEN'] = token
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['PERMISSION_MODE'] = 'auto'
os.system('python -m aetherion_cli.main chat " Hello Aetherion')
