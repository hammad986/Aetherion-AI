import asyncio
import os
from pathlib import Path
from aetherion_cli.tui.app import AetherionApp
from aetherion_cli.tui.state import state, Screen as AppScreen

async def main():
    docs_dir = Path("../../docs/screenshots")
    docs_dir.mkdir(parents=True, exist_ok=True)
    
    app = AetherionApp()
    
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.5)
        
        # Dashboard
        state.navigate(AppScreen.DASHBOARD)
        await pilot.pause(0.5)
        app.save_screenshot(str(docs_dir / "dashboard.svg"))
        
        # Chat
        state.navigate(AppScreen.CHAT)
        await pilot.pause(0.5)
        app.save_screenshot(str(docs_dir / "chat.svg"))
        
        # Sessions
        state.navigate(AppScreen.SESSIONS)
        await pilot.pause(0.5)
        app.save_screenshot(str(docs_dir / "sessions.svg"))
        
        print("Screenshots captured to docs/screenshots/")

if __name__ == "__main__":
    asyncio.run(main())
