# SanjivFlix Comment Reply Bot 🎬

SanjivFlix Facebook Page ke comments ka AI (Gemini) se reply dene wala bot.
Ye GitHub ke computers par chalta hai, isliye laptop ya phone on rakhne ki zaroorat nahi hai.

## Roz kaise use karein

1. Repo mein **Actions** tab kholiye.
2. **1 - Reply Banao**, phir **Run workflow** dabaiye. Kitne comments chahiye, likhiye (default 25). Phir **Run workflow** dabaiye.
3. 2-4 minute baad run par click kariye. Wahan saare comments aur unke AI reply ki list dikhegi.
4. Koi reply badalna ho to repo mein **drafts.txt** kholiye, ✏️ dabaiye, `REPLY:` ke aage ka text badliye aur Commit kariye.
   Koi reply nahi bhejna ho to uske aage `SKIP` likh dijiye.
5. **2 - Sab Bhejo**, phir **Run workflow** dabaiye. Saare reply Page par chale jaayenge, aur har comment par like bhi lag jaayega.

## Auto mode (baad mein)

**3 - Auto Reply** chalu hai: har 15 minute naye comments ka seedha reply bhejta hai.
Band karna ho to: Actions > 3 - Auto Reply > "..." > **Disable workflow**.

## AI ka andaaz badalna

**bot_instructions.txt** mein likha hai ki AI kaise reply kare: bhasha, lambai, kya na bole, kab SKIP kare.
Usme badlav karke Commit kar dijiye. Agli baar se reply naye tareeke se banenge.

## Zaroori Secrets (Settings > Secrets and variables > Actions)

| Naam | Kya hai |
|---|---|
| `GEMINI_API_KEY` | aistudio.google.com se mili key |
| `FB_PAGE_ID` | SanjivFlix Page ki ID |
| `FB_PAGE_TOKEN` | Page ka access token (kabhi expire nahi hone wala) |

⚠️ Ye teeno kabhi code ya kisi file mein mat likhiye. Sirf Secrets mein rakhiye.
