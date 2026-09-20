import os
from datetime import datetime

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

load_dotenv()

app = Flask(__name__)

api_key = os.getenv("ALPHA_VANTAGE_API_KEY")

if not api_key:
    raise RuntimeError(
        "ALPHA_VANTAGE_API_KEY environment variable is not set"
    )


app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    "sqlite:///stock_prices.db",
)

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


@app.route("/")
def main():
    return """
        <h1>Multi-Source Stock Price Aggregator</h1>

        <form action="/stock" method="GET">
            <input
                name="symbol"
                placeholder="Enter stock ticker, e.g. AAPL"
                required
            >

            <input
                type="submit"
                value="Fetch Stock Price"
            >
        </form>
    """


@app.route("/stock", methods=["GET"])
def stock():
    symbol = request.args.get(
        "symbol",
        "",
    ).strip().upper()

    if not symbol:
        return jsonify(
            {
                "error": "Please provide a stock symbol."
            }
        ), 400

    try:
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

    except requests.RequestException as error:
        return jsonify(
            {
                "error": "Failed to contact Alpha Vantage.",
                "details": str(error),
            }
        ), 502

    data = response.json()

    if "Note" in data:
        return jsonify(
            {
                "error": "Alpha Vantage API rate limit reached.",
                "details": data["Note"],
            }
        ), 429

    if "Information" in data:
        return jsonify(
            {
                "error": "Alpha Vantage returned an information message.",
                "details": data["Information"],
            }
        ), 429

    if "Error Message" in data:
        return jsonify(
            {
                "error": "Invalid stock symbol.",
                "details": data["Error Message"],
            }
        ), 400

    quote = data.get(
        "Global Quote",
        {},
    )

    price_value = quote.get(
        "05. price"
    )

    if not price_value:
        return jsonify(
            {
                "error": "No stock price was returned.",
                "symbol": symbol,
                "provider_response": data,
            }
        ), 502

    try:
        price = float(price_value)

    except ValueError:
        return jsonify(
            {
                "error": "The provider returned an invalid price."
            }
        ), 502

    stock_price = StockPrice(
        symbol=symbol,
        provider="Alpha Vantage",
        price=price,
    )

    db.session.add(stock_price)
    db.session.commit()

    return jsonify(
        {
            "id": stock_price.id,
            "symbol": stock_price.symbol,
            "provider": stock_price.provider,
            "price": stock_price.price,
            "fetched_at": stock_price.fetched_at.isoformat(),
        }
    )


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
                "fetched_at": item.fetched_at.isoformat(),
            }
            for item in prices
        ]
    )
    
@app.route("/api/stocks/<symbol>/analysis", methods=["GET"])
def stock_analysis(symbol):
    symbol = symbol.strip().upper()

    prices = (
        StockPrice.query
        .filter_by(symbol=symbol)
        .order_by(StockPrice.fetched_at.asc())
        .all()
    )

    if not prices:
        return jsonify({
            "error": f"No stored prices found for {symbol}"
        }), 404

    values = [item.price for item in prices]

    average_price = sum(values) / len(values)
    minimum_price = min(values)
    maximum_price = max(values)
    price_range = maximum_price - minimum_price

    return jsonify({
        "symbol": symbol,
        "number_of_observations": len(values),
        "average_price": round(average_price, 2),
        "minimum_price": round(minimum_price, 2),
        "maximum_price": round(maximum_price, 2),
        "price_range": round(price_range, 2),
    })


if __name__ == "__main__":
    app.run(debug=True)