from ustocks.web_app import app


if __name__ == "__main__":
    app.run(host="localhost", port=5001, debug=True, use_reloader=True)

