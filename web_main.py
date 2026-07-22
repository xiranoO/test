import uvicorn


if __name__ == "__main__":
    uvicorn.run("issue_agent.web:app", host="127.0.0.1", port=8000, reload=False)
