from flask import Flask, request

app = Flask(__name__)


@app.route("/")
def main():
    return """
        <h1>Multi-Source Stock Price Aggregator</h1>

        <form action="/echo_user_input" method="POST">
            <input
                name="user_input"
                placeholder="Enter stock ticker, e.g. AAPL"
            >
            <input type="submit" value="Search">
        </form>
    """


@app.route("/echo_user_input", methods=["POST"])
def echo_input():
    input_text = request.form.get("user_input", "")
    return "You entered: " + input_text


if __name__ == "__main__":
    app.run(debug=True)