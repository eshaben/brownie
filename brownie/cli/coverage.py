#!/usr/bin/python3

from copy import deepcopy
from docopt import docopt
from pathlib import Path
import sys
import json

from brownie.cli.test import get_test_files, run_test
from brownie.cli.utils import color
import brownie.network as network
from brownie.test.coverage import get_coverage_map
from brownie.utils.compiler import compile_contracts
import brownie._config as config

CONFIG = config.CONFIG

COVERAGE_COLORS = [
    (0.5, "bright red"),
    (0.85, "bright yellow"),
    (1, "bright green")
]

__doc__ = """Usage: brownie coverage [<filename>] [<range>] [options]

Arguments:
  <filename>          Only run tests from a specific file or folder
  <range>             Number or range of tests to run from file

Options:
  --help              Display this message
  --verbose           Enable verbose reporting
  --tb                Show entire python traceback on exceptions
  --always-transact   Perform all contract calls as transactions

Runs unit tests and analyzes the transaction stack traces to estimate
current test coverage. Results are saved to build/coverage.json"""


def main():
    args = docopt(__doc__)

    test_files = get_test_files(args['<filename>'])
    if len(test_files)==1 and args['<range>']:
        try:
            idx = args['<range>']
            if ':' in idx:
                idx = slice(*[int(i)-1 for i in idx.split(':')])
            else:
                idx = slice(int(idx)-1,int(idx))
        except:
            sys.exit("{0[error]}ERROR{0}: Invalid range. Must be an integer or slice (eg. 1:4)".format(color))
    elif args['<range>']:
        sys.exit("{0[error]}ERROR:{0} Cannot specify a range when running multiple tests files.".format(color))
    else:
        idx = slice(0, None)

    compiled = compile_contracts(
        Path(CONFIG['folders']['project']).joinpath('contracts')
    )
    fn_map_original = get_coverage_map(compiled)
    network.connect(config.ARGV['network'], True)

    if args['--always-transact']:
        CONFIG['test']['always_transact'] = True
    print("Contract calls will be handled as: {0[value]}{1}{0}".format(
        color,
        "transactions" if CONFIG['test']['always_transact'] else "calls"
    ))

    for filename in test_files:
        history, tb = run_test(filename, network, idx)
        if tb:
            sys.exit(
                "\n{0[error]}ERROR{0}: Cannot ".format(color) +
                "calculate coverage while tests are failing\n"
            )
        fn_map = deepcopy(fn_map_original)
        for tx in history:
            if not tx.receiver:
                continue
            for i in range(len(tx.trace)):
                t = tx.trace[i]
                pc = t['pc']
                name = t['contractName']
                source = t['source']['filename']
                if not name or not source:
                    continue
                try:
                    # find the function map item and record the tx
                    
                    fn = next(v for k,v in fn_map['contracts'][name][source].items() if pc in v['fn']['pc'])
                    print(fn)
                    fn['fn']['tx'].add(tx)
                    fn_map['counts'][name] += 1
                    if t['op']!="JUMPI":
                        # if not a JUMPI, find the line map item and record
                        next(i for i in fn['line'] if pc in i['pc'])['tx'].add(tx)
                        continue
                    # if a JUMPI, we need to have hit the jump pc AND a related opcode
                    ln = next(i for i in fn['line'] if pc==i['jump'])
                    if tx not in ln['tx']:
                        continue
                    # if the next opcode is not pc+1, the JUMPI was executed truthy
                    key = 'false' if tx.trace[i+1]['pc'] == pc+1 else 'true'
                    ln[key].add(tx)
                # pc didn't exist in map
                except StopIteration:
                    continue

        for contract in [k for k,v in fn_map['counts'].items() if not v]:
            del fn_map['contracts'][contract]
        del fn_map['counts']
        
        for contract, source, fn_name, maps in [(k,w,y,z) for k,v in fn_map['contracts'].items() for w,x in v.items() for y,z in x.items()]:

            fn = maps['fn']
            for ln in maps['line']:
                if ln['jump']:
                    ln['jump'] = [len(ln.pop('true')), len(ln.pop('false'))]
                ln['count'] = len(ln.pop('tx'))
                del ln['pc']

            fn['count'] = len(fn.pop('tx'))
            del fn['pc']
            if not fn['count'] or not [i for i in maps['line'] if i['count']]:
                del maps['line']
                fn['coverage'] = {'pct':0}
                continue

            count = 0
            fn['coverage'] = {'line':[], 'true':[], 'false':[]}
            for c,i in enumerate(maps['line']):
                if not i['count']:
                    continue
                if not i['jump'] or False not in i['jump']:
                    fn['coverage']['line'].append(c)
                    count+=2 if i['jump'] else 1
                    continue
                if i['jump'][0]:
                    fn['coverage']['true'].append(c)
                    count+=1
                if i['jump'][1]:
                    fn['coverage']['false'].append(c)
                    count+=1
            total = sum([1 if not i['jump'] else 2 for i in maps['line']])
            pct = count / total
            if count == total:
                del maps['line']
                fn['coverage'] = {'pct':1}
            else:
                fn['coverage']['pct']=round(count/total,2)
        
        # for contract in fn_map:
        #     for fn in fn_map[contract].copy():
        #         fn['count'] = len(fn.pop('tx'))
        #         del fn['pc']
        #         line_fn = [i for i in line_map[contract] if i['method']==fn['method']]
        #         if not fn['count'] or not [i for i in line_fn if i['count']]:
        #             for ln in line_fn:
        #                 line_map[contract].remove(ln)
        #         elif line_fn:
        #             fn_map[contract].remove(fn)
        #     fn_map[contract].extend(line_map[contract])

        # for contract in list(fn_map):
        #     fn_list = sorted(set(i['method'] for i in fn_map[contract] if i['method']))
        #     if not fn_list or not [i for i in fn_map[contract] if i['count']]:
        #         del fn_map[contract]
        path = Path(CONFIG['folders']['project'])
        path = path.joinpath("build/coverage"+filename[5:]+".json")
        for p in list(path.parents)[::-1]:
            if not p.exists():
                p.mkdir()
        json.dump(
            fn_map,
            path.open('w'),
            sort_keys=True,
            indent=4
        )

    exit()
    print("\nCoverage analysis complete!\n")
    for contract in fn_map:
        fn_list = sorted(set(i['method'] for i in fn_map[contract] if i['method']))
        if not fn_list:
            continue
        if not [i for i in fn_map[contract] if i['count']]:
            print("  contract: {0[contract]}{1}{0} - {0[bright red]}0.0%{0}".format(color, contract))
            continue
        print("  contract: {0[contract]}{1}{0}".format(color, contract))
        for fn in fn_list:
            map_ = [i for i in fn_map[contract] if i['method']==fn]
            count = 0
            for i in map_:
                if not i['count']:
                    continue
                if not i['jump']:
                    count+=1
                    continue
                if i['jump'][0]:
                    count+=1
                if i['jump'][1]:
                    count+=1
            total = sum([1 if not i['jump'] else 2 for i in map_])
            pct = count / total
            c = next(i[1] for i in COVERAGE_COLORS if pct<=i[0])
            print("    {0[contract_method]}{1}{0} - {2}{3:.1%}{0}".format(
                color, fn, color(c), pct
            ))
        print()
    print("\nDetailed results saved to {0[string]}build/coverage.json{0}".format(color))