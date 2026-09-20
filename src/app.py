import os
import time
from datetime import datetime

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from prometheus_client import Counter


load_dotenv()

app = Flask(__name__)


# ---------------------------------------------------------
# Metrics
# ---------------------------------------------------------

APP_START_TIME = time.time()

REQUEST_COUNT = Counter(
    "stock_price_aggregator_http_requests_total",
    "Total number of HTTP requests received",
)


# ---------------------------------------------------------
# Database
# ---------------------------------------------------------

database_url = os.getenv(
    "DATABASE_URL",
    "sqlite:///stock_prices.db",
)

# Heroku may provide postgres:// while SQLAlchemy expects postgresql://
if database_url.startswith("postgres://"):
    database_url = database_url.replace(
        "postgres://",
        "postgresql://",
        1,
    )

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)


class StockPrice(db.Model):
    __tablename__ = "stock_prices"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    symbol = db.Column(
        db.String(20),
        nullable=False,
        index=True,
    )

    provider = db.Column(
        db.String(50),
        nullable=False,
    )

    price = db.Column(
        db.Float,
        nullable=False,
    )

    fetched_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.now,
    )


# ---------------------------------------------------------
# Stock provider functions
# ---------------------------------------------------------

def fetch_alpha_vantage_price(symbol):
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY")

    if not api_key:
        raise RuntimeError(
            "ALPHA_VANTAGE_API_KEY is not configured"
        )

    response = requests.get(
        "https://www.alphavantage.co/query",
        params={
            "function": "GLOBAL_QUOTE",
            "symbol": symbol,
            "apikey": api_key,
        },
        timeout=10,
    )

    response.raise_for_status()

    data = response.json()

    quote = data.get("Global Quote", {})
    price_value = quote.get("05. price")

    if not price_value:
        return None

    return float(price_value)


def fetch_twelve_data_price(symbol):
    api_key = os.getenv("TWELVE_DATA_API_KEY")

    if not api_key:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY is not configured"
        )

    response = requests.get(
        "https://api.twelvedata.com/price",
        params={
            "symbol": symbol,
            "apikey": api_key,
        },
        timeout=10,
    )

    response.raise_for_status()

    data = response.json()

    price_value = data.get("price")

    if not price_value:
        return None

    return float(price_value)


def fetch_marketstack_price(symbol):
    api_key = os.getenv("MARKETSTACK_API_KEY")

    if not api_key:
        raise RuntimeError(
            "MARKETSTACK_API_KEY is not configured"
        )

    response = requests.get(
        "https://api.apilayer.net/marketstack/v2/eod",
        params={
            "access_key": api_key,
            "symbols": symbol,
            "sort": "DESC",
            "limit": 1,
        },
        timeout=10,
    )

    response.raise_for_status()

    data = response.json()

    records = data.get("data", [])

    if not records:
        return None

    price_value = records[0].get("close")

    if price_value is None:
        return None

    return float(price_value)


# ---------------------------------------------------------
# Aggregation
# ---------------------------------------------------------

def fetch_provider_prices(symbol):
    providers = [
        (
            "Alpha Vantage",
            fetch_alpha_vantage_price,
        ),
        (
            "Twelve Data",
            fetch_twelve_data_price,
        ),
        (
            "Marketstack",
            fetch_marketstack_price,
        ),
    ]

    provider_prices = {}
    provider_errors = {}

    for provider_name, fetch_price in providers:
        try:
            price = fetch_price(symbol)

            if price is not None:
                provider_prices[provider_name] = price
            else:
                provider_errors[provider_name] = (
                    "No price returned"
                )

        except (
            requests.RequestException,
            RuntimeError,
            ValueError,
        ) as error:
            provider_errors[provider_name] = str(error)

    return provider_prices, provider_errors


def save_provider_prices(symbol, provider_prices):
    for provider, price in provider_prices.items():
        stock_price = StockPrice(
            symbol=symbol,
            provider=provider,
            price=price,
        )

        db.session.add(stock_price)

    db.session.commit()


def build_aggregate_result(symbol):
    provider_prices, provider_errors = (
        fetch_provider_prices(symbol)
    )

    if not provider_prices:
        return None, provider_errors

    average_price = (
        sum(provider_prices.values())
        / len(provider_prices)
    )

    save_provider_prices(
        symbol,
        provider_prices,
    )

    result = {
        "symbol": symbol,
        "provider_count": len(provider_prices),
        "providers": provider_prices,
        "average_price": round(
            average_price,
            2,
        ),
        "provider_errors": provider_errors,
    }

    return result, provider_errors


# ---------------------------------------------------------
# Request metrics
# ---------------------------------------------------------

@app.before_request
def count_request():
    if request.endpoint != "metrics":
        REQUEST_COUNT.inc()


# ---------------------------------------------------------
# Monitoring routes
# ---------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok"
    }), 200


@app.route("/metrics", methods=["GET"])
def metrics():
    uptime_seconds = (
        time.time()
        - APP_START_TIME
    )

    total_requests = (
        REQUEST_COUNT
        ._value
        .get()
    )

    requests_per_second = (
        total_requests / uptime_seconds
        if uptime_seconds > 0
        else 0
    )

    return jsonify({
        "total_requests": int(total_requests),
        "uptime_seconds": round(
            uptime_seconds,
            2,
        ),
        "requests_per_second": round(
            requests_per_second,
            4,
        ),
    }), 200


# ---------------------------------------------------------
# Main page
# ---------------------------------------------------------

@app.route("/")
def main():
    return """
        <h1>Multi-Source Stock Price Aggregator</h1>

        <p>
            Enter a stock symbol to retrieve prices from
            Alpha Vantage, Twelve Data, and Marketstack.
        </p>

        <form action="/stock" method="GET">
            <input
                name="symbol"
                placeholder="Enter stock ticker, e.g. AAPL"
                required
            >

            <input
                type="submit"
                value="Fetch Stock Prices"
            >
        </form>
    """


# ---------------------------------------------------------
# Main stock aggregation route
# ---------------------------------------------------------

@app.route("/stock", methods=["GET"])
def stock():
    symbol = request.args.get(
        "symbol",
        "",
    ).strip().upper()

    if not symbol:
        return jsonify({
            "error": "Please provide a stock symbol."
        }), 400

    result, provider_errors = (
        build_aggregate_result(symbol)
    )

    if result is None:
        return jsonify({
            "error": (
                "No stock price providers returned data."
            ),
            "symbol": symbol,
            "provider_errors": provider_errors,
        }), 502

    return jsonify(result), 200


# ---------------------------------------------------------
# REST aggregation endpoint
# ---------------------------------------------------------

@app.route(
    "/api/stocks/<symbol>/aggregate",
    methods=["GET"],
)
def aggregate_stock_price(symbol):
    symbol = symbol.strip().upper()

    if not symbol:
        return jsonify({
            "error": "Please provide a stock symbol."
        }), 400

    result, provider_errors = (
        build_aggregate_result(symbol)
    )

    if result is None:
        return jsonify({
            "error": (
                "No stock price providers returned data."
            ),
            "symbol": symbol,
            "provider_errors": provider_errors,
        }), 502

    return jsonify(result), 200


# ---------------------------------------------------------
# Historical stored prices
# ---------------------------------------------------------

@app.route(
    "/api/stocks/<symbol>/history",
    methods=["GET"],
)
def stock_history(symbol):
    symbol = symbol.strip().upper()

    prices = (
        StockPrice.query
        .filter_by(symbol=symbol)
        .order_by(
            StockPrice.fetched_at.desc()
        )
        .all()
    )

    return jsonify(
        [
            {
                "id": item.id,
                "symbol": item.symbol,
                "provider": item.provider,
                "price": item.price,
                "fetched_at": (
                    item.fetched_at.isoformat()
                ),
            }
            for item in prices
        ]
    )


# ---------------------------------------------------------
# Historical analysis
# ---------------------------------------------------------

@app.route(
    "/api/stocks/<symbol>/analysis",
    methods=["GET"],
)
def stock_analysis(symbol):
    symbol = symbol.strip().upper()

    prices = (
        StockPrice.query
        .filter_by(symbol=symbol)
        .order_by(
            StockPrice.fetched_at.asc()
        )
        .all()
    )

    if not prices:
        return jsonify({
            "error": (
                f"No stored prices found for {symbol}"
            )
        }), 404

    values = [
        item.price
        for item in prices
    ]

    average_price = (
        sum(values)
        / len(values)
    )

    minimum_price = min(values)
    maximum_price = max(values)

    price_range = (
        maximum_price
        - minimum_price
    )

    return jsonify({
        "symbol": symbol,
        "number_of_observations": len(values),
        "average_price": round(
            average_price,
            2,
        ),
        "minimum_price": round(
            minimum_price,
            2,
        ),
        "maximum_price": round(
            maximum_price,
            2,
        ),
        "price_range": round(
            price_range,
            2,
        ),
    })


if __name__ == "__main__":
    app.run(debug=True)