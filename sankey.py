import json
import argparse
import subprocess
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Toplevel account categories that you have in your chart of accounts.
# Used to filter out non-account entries from the JSON balance report
TOPLEVEL_ACCOUNT_CATEGORIES=['income','expenses','assets','liabilities','virtual']

# assets:cash -> assets
# assets -> ''
def parent(account_name):
    return ':'.join(account_name.split(':')[:-1])

def read_balance_report(filename,account_categories):
    # You might want to try just "income expenses" as account categories, or less depth via "--depth 2"
    # Explanation for the choice of arguments:
    # "balance income expenses assets liabilities" are account categories
    # "not:desc:opening" excludes year-open transaction which carries over values of assets from the previous year, as we are only interested in asset increases, not
    #     absolute value
    # "--cost --value=then,£ --infer-value" - convert everything to a single commodity, £ in my case
    # "--no-total" - ensure that we dont have a total row
    # "--tree --no-elide" - ensure that parent accounts are listed even if they dont have balance changes, to make sure that our sankey flows dont have gaps
    # "-O json" to produce JSON output
    command = 'hledger -f %s balance %s not:desc:opening --cost --value=then,£ --infer-value --no-total --tree --no-elide -O json' % (filename,account_categories)

    process_output = subprocess.run(command.split(' '), stdout=subprocess.PIPE, text=True).stdout

    # Parse JSON output
    data = json.loads(process_output)

    # First element of the JSON array contains the account entries
    accounts = data[0]

    # Build list of (account_name, balance) tuples
    balances = []
    for entry in accounts:
        account_name = entry[0]
        # Filter to only include accounts that match our top-level categories
        if any(cat in account_name for cat in TOPLEVEL_ACCOUNT_CATEGORIES):
            # Get the balance from the amounts array (entry[3])
            amounts = entry[3]
            if amounts:
                balance = amounts[0]["aquantity"]["floatingPoint"]
            else:
                balance = 0
            balances.append((account_name, balance))

    return balances

# Convert hledger balance report into a list of (source, target, value) tuples for the sankey graph.
# We make the following assumptions:
# 1. Balance report will have top-level categories "assents","income","expenses","liabilities" with the usual semantics.
#    I also have "virtual:assets profit and loss" for unrealized P&L, which also matches this query.
# 2. For sankey diagram, we want to see how "income" is being used to cover "expenses", increas the value of "assets" and pay off "liabilities", so we assume that
#    by default the money are flowing from income to the other categores.
# 3. However, positive income or negative expenses/assets/liabilities would be correctly treated as money flowing against the "usual" direction
def to_sankey_data(balances):
    # List to store (source, target, value) tuples
    sankey_data = []

    # A set of all accounts mentioned in the report, to check that parent accounts have known balance
    accounts = set(account_name for account_name, _ in balances)

    # Convert report to sankey data
    for account_name, balance in balances:
        # top-level accounts need to be connected to the special "pot" intermediate bucket
        # We assume that "income" and "virtual" accounts contribute to pot, while expenses draw from it
        if account_name in TOPLEVEL_ACCOUNT_CATEGORIES:
            parent_acc = 'pot'
        else:
            parent_acc = parent(account_name)
            if parent_acc not in accounts:
                raise Exception(f'for account {account_name}, parent account {parent_acc} not found - have you forgotten --no-elide?')

        # income and virtual flow 'up'
        if 'income' in account_name or 'virtual' in account_name:
            # Negative income is just income, positive income is a reduction, pay-back or something similar
            # For sankey, all flow values should be positive
            if balance < 0:
                source, target = account_name, parent_acc
            else:
                source, target = parent_acc, account_name
        else:
            # positive expenses/assets are normal expenses or investements or purchase of assets, negative values are cashbacks, or cashing in of investments
            if balance >= 0:
                source, target = parent_acc, account_name
            else:
                source, target = account_name, parent_acc

        sankey_data.append((source, target, abs(balance)))

    return sankey_data

def sankey_plot(sankey_data):
    # Sort by (target, source) to keep related accounts close together in the initial layout
    sankey_data = sorted(sankey_data, key=lambda x: (x[1], x[0]))

    # Get unique node names
    nodes = list(dict.fromkeys(
        [source for source, _, _ in sankey_data] +
        [target for _, target, _ in sankey_data]
    ))

    # Create Sankey diagram
    fig = go.Figure(data=[go.Sankey(
        node=dict(
            pad=25,
            thickness=20,
            line=dict(color="black", width=0.5),
            label=nodes,
            color="blue"
        ),
        link=dict(
            source=[nodes.index(source) for source, _, _ in sankey_data],
            target=[nodes.index(target) for _, target, _ in sankey_data],
            value=[value for _, _, value in sankey_data]
        ))])

    return fig

def expenses_treemap_plot(balances):
    # Filter to only expenses
    expenses = [(name, value) for name, value in balances if 'expenses' in name]

    labels = [name for name, _ in expenses]
    values = [value for _, value in expenses]
    parents = [parent(name) for name, _ in expenses]

    fig = go.Figure(go.Treemap(
        labels=labels,
        parents=parents,
        values=values,
        branchvalues='total'
    ))

    return fig


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate Sankey diagrams from hledger balance reports')
    parser.add_argument('filename', help='Path to the hledger journal file')
    parser.add_argument('--debug', action='store_true', help='Print debug output (parsed data)')
    args = parser.parse_args()

    filename = args.filename
    debug = args.debug

    # Sankey graph for all balances/flows
    all_balances = read_balance_report(filename,'income expenses assets liabilities')
    if debug:
        print("=" * 60)
        print("All balances (income expenses assets liabilities):")
        print("=" * 60)
        for account, balance in all_balances:
            print(f"  {account:40} {balance:>10.2f}")
        print()

    all_balances_sankey = to_sankey_data(all_balances)
    if debug:
        print("=" * 60)
        print("All balances Sankey data:")
        print("=" * 60)
        for source, target, value in all_balances_sankey:
            print(f"  {source:30} -> {target:30} {value:>10.2f}")
        print()

    all_balances_fig = sankey_plot(all_balances_sankey)

    # Sankey graph for just income/expenses
    income_expenses = read_balance_report(filename,'income expenses')
    if debug:
        print("=" * 60)
        print("Income/Expenses:")
        print("=" * 60)
        for account, balance in income_expenses:
            print(f"  {account:40} {balance:>10.2f}")
        print()

    income_expenses_sankey = to_sankey_data(income_expenses)
    if debug:
        print("=" * 60)
        print("Income/Expenses Sankey data:")
        print("=" * 60)
        for source, target, value in income_expenses_sankey:
            print(f"  {source:30} -> {target:30} {value:>10.2f}")
        print()

    income_expenses_fig = sankey_plot(income_expenses_sankey)

    # Expenses treemap plot for just expenses
    expenses_fig = expenses_treemap_plot(income_expenses)

    # Display all three graphs in a column
    fig = make_subplots(rows=3, cols=1, specs = [[{"type": "treemap"}],[{"type": "sankey"}],[{"type": "sankey"}]] )

    # Expenses treemap first
    fig.add_trace(expenses_fig.data[0], row=1, col=1)
    # ... followed by income-to-expenses flows
    fig.add_trace(income_expenses_fig.data[0], row=2, col=1)
    # ... followed by flows between all the balances
    fig.add_trace(all_balances_fig.data[0], row=3, col=1)
    fig.update_layout(title_text="Cash Flows", height=2700) # 3 plots x 900 px

    fig.show()
