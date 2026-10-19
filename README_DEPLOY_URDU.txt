ClipForge — Website Live Karne Ka Aasaan Tareeqa (100% FREE, koi card nahi)
===============================================================================
Render card mangta hai — is liye hum STREAMLIT CLOUD par deploy karein ge.
Bilkul free hai, credit card NAHI mangta. Sirf GitHub account chahiye
(wo aap ke paas hai).

PEHLE: GitHub repo update karein (2 minute)
1. Apne repo (17tiktokofficial-sketch/clipforge) mein jayein.
2. "Add file" → "Upload files" → is update-zip ki files upload karein:
   - streamlit_app.py  (nayi file)
   - requirements.txt  (nayi wali — purani ko replace kar dega)
   - README_DEPLOY_URDU.txt (ye guide)
3. "Commit changes" dabayein.

PHIR: Streamlit Cloud par deploy (5 minute + wait)
1. share.streamlit.io kholein → "Sign in" → "Continue with GitHub".
2. "New app" / "Create app" dabayein.
3. Ye select karein:
   - Repository: 17tiktokofficial-sketch/clipforge
   - Branch: main
   - Main file path: streamlit_app.py
4. "Deploy" dabayein. 5-10 minute mein aap ki website ka link mil
   jayega, jaise: https://clipforge-xxxx.streamlit.app

AAKHIR MEIN: Apna login lagayein (1 minute)
1. Streamlit dashboard mein apni app kholein → neeche ⋮ (menu) →
   "Settings" → "Secrets".
2. Ye likhein:
   CF_USER = "apna-username"
   CF_PASS = "apna-password"
3. Save karein. Ab yehi username/password aap ki website ka login hai.

ZAROORI BAATEIN:
- Free app istemal na ho to "so" jati hai; link kholne par ~1 minute
  mein jaag jati hai. Ye normal hai.
- Lambi (2 ghante wali) video par free computer slow hoga —
  ye testing ke liye theek hai.
- YouTube kabhi kabhi download block karta hai — us waqt video
  file seedha upload kar dein (option app mein hai).
