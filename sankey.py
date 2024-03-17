import sys
import pandas as pd
import subprocess
from io import StringIO
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px
from pprint import pformat

# Toplevel account categories that you have in your chart of accounts.
# Used to filter out non-account lines out of the csv balance report
TOPLEVEL_ACCOUNT_CATEGORIES=['income','revenues','expenses','assets','liabilities','virtual']

# Account name substrings for recognising account types
ASSET_ACCOUNT_PAT     = 'assets'
LIABILITY_ACCOUNT_PAT = 'liabilities'
INCOME_ACCOUNT_PAT    = 'income'
EXPENSE_ACCOUNT_PAT   = 'expenses'

HLEDGER_EXTRA_ARGS = ''

verbosity = 0

# Pretty print a value if global verbosity level is high enough, and return it.
# label will be prepended if non-empty.
def dbg(level, label, val, pretty=True):
    if verbosity >= level: print( (label + ":\n" if label else '') + (pformat(val) if pretty else val) )
    return val
def d1(label,val,pretty=True): return dbg(1,label,val,pretty)
def d2(label,val,pretty=True): return dbg(2,label,val,pretty)
def d3(label,val,pretty=True): return dbg(3,label,val,pretty)

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
    # "-O csv" to produce CSV output
    command = 'hledger -f %s balance %s not:desc:opening --cost --value=then,£ --infer-value --no-total --tree --no-elide -O csv' % (filename,account_categories)
    command += ' ' + HLEDGER_EXTRA_ARGS
    d1('command',command,0)

    process_output = subprocess.run(command.split(' '), stdout=subprocess.PIPE, text=True).stdout
    d2('hledger output lines',process_output)

    # Read the process output into a DataFrame, and clean it up, removing headers
    df = pd.read_csv(StringIO(process_output), header=None)
    df = df[df[0].str.contains('|'.join(TOPLEVEL_ACCOUNT_CATEGORIES))]

    # Remove "£" sign from balance values, and convert them to numeric
    df[1] = df[1].str.replace('£', '')
    df[1] = pd.to_numeric(df[1], errors='coerce')

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
        if INCOME_ACCOUNT_PAT in account_name or 'virtual' in account_name:
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

    # Output the sankey_df to a CSV file, for debugging
    sankey_df.to_csv('sankey.csv', index=False)
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
    balances_df = balances_df[balances_df[0].str.contains(EXPENSE_ACCOUNT_PAT)].copy()  # Make a copy to avoid modifying the original DataFrame
    balances_df.loc[:, 'name'] = balances_df[0]
    balances_df.loc[:, 'value'] = balances_df[1].astype(int)
    balances_df.loc[:, 'parent'] = balances_df['name'].apply(parent)
    return px.treemap(data_frame=balances_df, names='name', parents='parent', values='value', branchvalues='total')
   

if __name__ == "__main__":
    filename=sys.argv[1]
    
    # Sankey graph for all balances/flows
    all_balances_df = read_balance_report(filename, INCOME_ACCOUNT_PAT + ' ' + EXPENSE_ACCOUNT_PAT + ' ' + ASSET_ACCOUNT_PAT + ' ' + LIABILITY_ACCOUNT_PAT)
    d1('all_balances_df',all_balances_df)

    all_balances = sankey_plot(to_sankey_df(all_balances_df))
    d2('all_balances sankey plot',all_balances)

    # Sankey graph for just income/expenses
    income_expenses_df = read_balance_report(filename, INCOME_ACCOUNT_PAT + ' ' + EXPENSE_ACCOUNT_PAT)
    d1('income_expenses_df',income_expenses_df)
    income_expenses = sankey_plot(to_sankey_df(income_expenses_df))
    d2('all_balances sankey plot',income_expenses)

    # Expenses treemap plot for just expenses
    expenses = expenses_treemap_plot(income_expenses_df)
    d2('expenses treemap plot',expenses)

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
