from app import app

# For Vercel
application = app

# For local development
if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", 8002))
    app.run(host="0.0.0.0", port=port, debug=False)
