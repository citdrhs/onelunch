from werkzeug.middleware.proxy_fix import ProxyFix
from app import app

app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1, x_proto=1, x_host=1)

if __name__ == "__main__":
    app.run()