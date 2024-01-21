
def get_income_entries():
    # Initialize an empty list to store income entries
    income_entries = []

    # Get the number of income entries from the user
    num_entries = int(input("Enter the number of income entries: "))

    # Input income entries
    for i in range(num_entries):
        while True:
            try:
                # Prompt the user for each income entry
                income = float(input(f"Enter income for entry {i + 1}: "))
                # Validate that income is a positive number
                if income < 0:
                    raise ValueError("Income must be a positive number.")
                break
            except ValueError as e:
                print(f"Error: {e}")

        # Add validated income to the list
        income_entries.append(income)

    return income_entries

def calculate_average_income(income_entries):
    # Check if the list of income entries is not empty
    if not income_entries:
        return "No income entries found."

    # Calculate the total income
    total_income = sum(income_entries)

    # Calculate the average income
    average_income = total_income / len(income_entries)

    return average_income

# Get income entries from the user
income_entries = get_income_entries()

# Calculate and display the average income
average_income = calculate_average_income(income_entries)
print(f"The average income is: {average_income}")
```

#This code includes error handling to ensure that the user inputs valid positive numbers for each income entry. The `get_income_entries` function prompts the user for the number of entries and then collects and validates each income entry. The `calculate_average_income` function takes the list of income entries as input and calculates the average income.
