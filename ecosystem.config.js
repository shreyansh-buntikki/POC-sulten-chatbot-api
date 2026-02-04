module.exports = {
  apps: [
    {
      name: "fastapi-app",
      script: "apps/fastapi/app.py",
      interpreter: "python3.12",
      env: {
        PYTHONPATH: process.env.PYTHONPATH + ":" + process.cwd(),
        PORT: 5000,  // Change to your FastAPI port
      }
    },
  ]
};
