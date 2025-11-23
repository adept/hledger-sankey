import sys
import json
import argparse
import pandas as pd
import subprocess
from io import StringIO
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px

# Toplevel account categories that you have in your chart of accounts.
# Used to filter out non-account lines out of the csv balance report
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

    # Build list of rows for the DataFrame
    rows = []
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
            rows.append([account_name, balance])

    df = pd.DataFrame(rows, columns=[0, 1])
    return df

# Convert hledger balance report dataframe into a (source, target, cash flow value) table, that could be used to produce the sankey graph.
# We make the following assumptions:
# 1. Balance report will have top-level categories "assents","income","expenses","liabilities" with the usual semantics.
#    I also have "virtual:assets profit and loss" for unrealized P&L, which also matches this query.
# 2. For sankey diagram, we want to see how "income" is being used to cover "expenses", increas the value of "assets" and pay off "liabilities", so we assume that
#    by default the money are flowing from income to the other categores.
# 3. However, positive income or negative expenses/assets/liabilities would be correctly treated as money flowing against the "usual" direction
def to_sankey_df(df):
    # Create a DataFrame to store the sankey data
    sankey_df = pd.DataFrame(columns=['source', 'target', 'value'])

    # A set of all accounts mentioned in the report, to check that parent accounts have known balance
    accounts=set(df[0].values)

    # Convert report to the sankey dataframe
    for index, row in df.iterrows():
        account_name = row[0]
        balance = row[1]

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
                source, target = parent_acc,   account_name
        else:
            # positive expenses/assets are normal expenses or investements or purchase of assets, negative values are cashbacks, or cashing in of investments
            if balance >= 0:
                source, target = parent_acc,   account_name
            else:
                source, target = account_name, parent_acc

        sankey_df.loc[len(sankey_df)] = {'source': source, 'target': target, 'value': abs(balance)}

    return sankey_df

def sankey_plot(sankey_df):
    # Sort DataFrame by either 'source' or 'target' column, to make sure that related accounts stay close together in the initial layout
    sankey_df.sort_values(by=['target', 'source'], inplace=True)

    # Get unique sources and targets for node names
    nodes = pd.concat([sankey_df['source'], sankey_df['target']]).unique()

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
            source=sankey_df['source'].map(lambda x: nodes.tolist().index(x)),
            target=sankey_df['target'].map(lambda x: nodes.tolist().index(x)),
            value=sankey_df['value']
        ))])

    return fig

def expenses_treemap_plot(balances_df):
    balances_df = balances_df[balances_df[0].str.contains('expenses')].copy()  # Make a copy to avoid modifying the original DataFrame
    balances_df.loc[:, 'name'] = balances_df[0]
    balances_df.loc[:, 'value'] = balances_df[1].astype(int)
    balances_df.loc[:, 'parent'] = balances_df['name'].apply(parent)
    return px.treemap(data_frame=balances_df, names='name', parents='parent', values='value', branchvalues='total')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate Sankey diagrams from hledger balance reports')
    parser.add_argument('filename', help='Path to the hledger journal file')
    parser.add_argument('--debug', action='store_true', help='Print debug output (parsed DataFrames)')
    args = parser.parse_args()

    filename = args.filename
    debug = args.debug

    # Sankey graph for all balances/flows
    all_balances_df = read_balance_report(filename,'income expenses assets liabilities')
    if debug:
        print("=" * 60)
        print("All balances DataFrame (income expenses assets liabilities):")
        print("=" * 60)
        print(all_balances_df.to_string())
        print()

    all_balances_sankey_df = to_sankey_df(all_balances_df)
    if debug:
        print("=" * 60)
        print("All balances Sankey DataFrame:")
        print("=" * 60)
        print(all_balances_sankey_df.to_string())
        print()

    all_balances = sankey_plot(all_balances_sankey_df)

    # Sankey graph for just income/expenses
    income_expenses_df = read_balance_report(filename,'income expenses')
    if debug:
        print("=" * 60)
        print("Income/Expenses DataFrame:")
        print("=" * 60)
        print(income_expenses_df.to_string())
        print()

    income_expenses_sankey_df = to_sankey_df(income_expenses_df)
    if debug:
        print("=" * 60)
        print("Income/Expenses Sankey DataFrame:")
        print("=" * 60)
        print(income_expenses_sankey_df.to_string())
        print()

    income_expenses = sankey_plot(income_expenses_sankey_df)

    # Expenses treemap plot for just expenses
    expenses = expenses_treemap_plot(income_expenses_df)

    # Display all three graphs in a column
    fig = make_subplots(rows=3, cols=1, specs = [[{"type": "treemap"}],[{"type": "sankey"}],[{"type": "sankey"}]] )

    # Expenses treemap first
    fig.add_trace(expenses.data[0], row=1, col=1)
    # ... followed by income-to-expenses flows
    fig.add_trace(income_expenses.data[0], row=2, col=1)
    # ... followed by flows between all the balances
    fig.add_trace(all_balances.data[0], row=3, col=1)
    fig.update_layout(title_text="Cash Flows", height=2700) # 3 plots x 900 px

    fig.show()
