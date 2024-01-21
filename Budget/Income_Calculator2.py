import sqlite3

def connect_to_database(database_path):
    try:
        # Connect to the SQLite database
        conn = sqlite3.connect(database_path)
        print("Connected to the database.")
        return conn
    except sqlite3.Error as e:
        print(f"Error connecting to the database: {e}")
        return None

def get_credit_transactions_for_month(conn, month, year):
    try:
        # Create a cursor object to execute SQL queries
        cursor = conn.cursor()

        # Execute a query to get credit transactions for the specified month and year
        query = f"SELECT amount FROM transactions WHERE type='credit' AND strftime('%Y-%m', date) = '{year}-{month}'"
        cursor.execute(query)

        # Fetch all credit transactions
        credit_transactions = cursor.fetchall()

        return credit_transactions
    except sqlite3.Error as e:
        print(f"Error retrieving credit transactions: {e}")
        return None

def calculate_average_income_for_six_months(conn, current_month, current_year):
    try:
        # Create a cursor object to execute SQL queries
        cursor = conn.cursor()

        # Calculate the average income for the previous six months
        total_income = 0
        total_months = 0

        for i in range(1, 7):
            # Calculate the month and year for the ith previous month
            previous_month = (current_month - i) % 12
            previous_year = current_year - 1 if previous_month == 0 else current_year

            # Execute a query to get credit transactions for the previous month
            query = f"SELECT amount FROM transactions WHERE type='credit' AND strftime('%Y-%m', date) = '{previous_year}-{previous_month:02}'"
            cursor.execute(query)

            # Fetch all credit transactions for the previous month
            credit_transactions = cursor.fetchall()

            # Calculate the total income for the previous months
            total_income += sum(transaction[0] for transaction in credit_transactions)
            total_months += 1

        # Calculate the average income
        average_income = total_income / total_months if total_months > 0 else 0

        return average_income
    except sqlite3.Error as e:
        print(f"Error calculating average income: {e}")
        return None

def main():
    # Replace 'your_database.db' with the actual path to your database file
    database_path = 'your_database.db'

    # Replace with the desired month (e.g., '01' for January) and year
    selected_month = 1
    selected_year = 2024

    # Connect to the database
    conn = connect_to_database(database_path)
    if conn is None:
        return

    # Get credit transactions for the specified month and year
    credit_transactions = get_credit_transactions_for_month(conn, selected_month, selected_year)

    # Calculate and display the average income for the previous six months
    average_income = calculate_average_income_for_six_months(conn, selected_month, selected_year)
    print(f"The average income for the previous six months is: {average_income}")

    # Close the database connection
    conn.close()
    print("Disconnected from the database.")

if __name__ == "__main__":
    main()
  
