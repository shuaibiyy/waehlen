# coding=utf-8
# Written using python 3.6
import sys
import csv
import re
import requests
import shapefile
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pprint import pprint
from functools import reduce
from collections import Counter
from bs4 import BeautifulSoup
from mpl_toolkits.basemap import Basemap

plt.rcdefaults()


def fetch_rows(csv_path):
    """Open a CSV file and read its lines."""
    rows = []

    with open(csv_path, 'rt', encoding='utf8') as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(row)

    return rows


def get_csv_values(csv_path):
    """Return the semi-colon separated values in a csv as a list."""
    rows = fetch_rows(csv_path)

    # Remove column headings
    del rows[0]

    return [x[0].split(';') for x in rows]


CSV_VOTES = get_csv_values('./ergebnisse.csv')

CSV_POPULATION = get_csv_values('./population.csv')

ALTERNATE_NAMES = [
    ['DIE LINKE', ['DIE LINKE.']],
    ['GRÜNE', ['GRÜNE/B 90', 'EB: Gruner']],
    ['ÖDP', ['ÖDP / Familie ..']]
]


def flatten(ls):
    """Flatten a nested list by 1 level.
    >>> flatten([[]])
    []
    >>> flatten([[(1,2)], [(4,5)]])
    [(1, 2), (4, 5)]
    """
    return [item for sublist in ls for item in sublist]


def filter_not_parties(values):
    """Remove entries that are not for parties."""
    not_parties = ['Wahlberechtigte', 'Wähler', 'Ungültige', 'Gültige']
    return [x for x in values if x[2] not in not_parties]


def values_at(csv_values, index):
    """Return all values at an index in the csv."""

    filtered = filter_not_parties(csv_values)

    return [x[index] for x in filtered]


def unique_values_at(csv_values, index):
    """Return all unique values at an index in the csv."""
    return set(values_at(csv_values, index))


def values_by(csv_values, filter_text, index):
    """Return values filtered by the text at an index."""
    return [x for x in csv_values if x[index] == filter_text]


def party_second_vote(party, value):
    """Return a zweitstimmen if it matches a party."""
    if value[2] == party:
        # zweitstimmen is at index #4.
        return value[4]

    return '-'


def party_second_votes(party, values):
    """Return all zweitstimmen for a party."""
    return [party_second_vote(party, x) for x in values]


def filter_dashes(kv):
    """Remove dashes from seconds in a 2-tuple."""
    return kv[0], [x for x in kv[1] if x != '-']


def aggregate(kv):
    """Aggregate values of a list second element in a 2-tuple.
    >>> aggregate(('foo', [5, 4, 6]))
    ('foo', 15)
    >>> aggregate(('bar', []))
    ('bar', 0)
    """
    return kv[0], reduce(lambda acc, x: acc + int(x), kv[1], 0)


def total(values):
    """Sum up values of all seconds in a list of 2-tuples.
    >>> total([('foo', 304), ('bar', 435)])
    739
    >>> total([])
    0
    """
    return reduce(lambda acc, kv: acc + int(kv[1]), values, 0)


def lookup_alt_names(alternate_names, party):
    """Look up a party's alternate names.
    >>> lookup_alt_names([['foo', ['bar', 'baz']], ['boo', []]], ('foo', 443))
    ['bar', 'baz']
    >>> lookup_alt_names([['', []], ['boo', []]], ('boo', 0))
    []
    """
    maybe = [x for x in alternate_names if x[0].upper() == party[0].upper()]

    if not maybe:
        return []

    return maybe[0][1]


def lookup_1st_value(values, match_text):
    """Return the first matching tuple of first values in a list of tuples.
    >>> lookup_1st_value([('foo', 65), ('bar', 43), ('baz', 23), ('bar', 67)], 'bar')
    ('bar', 43)
    >>> lookup_1st_value([], 'foo')
    ('foo', 0)
    """
    maybe = [x for x in values if x[0].upper() == match_text.upper()]

    if not maybe:
        return match_text, 0

    return maybe[0]


def lookup_state_name(state_id):
    """Look up the name of a state by its ID."""
    state_names = [(x[0], x[1]) for x in CSV_POPULATION]
    return lookup_1st_value(state_names, state_id)[1]


def merge_alt_names(alternate_names, alternates_with_votes, party):
    """Merge the votes of a party with votes of its known alternate names."""
    matched_alt_names = lookup_alt_names(alternate_names, party)
    matched_votes = [lookup_1st_value(alternates_with_votes, x) for x in matched_alt_names]
    matched_votes.append(party)

    return party[0], total(matched_votes)


def merge_parties_alt_names(parties_votes):
    """Merge the votes of parties with votes of their known alternate names."""
    all_alternate_names = [x.upper() for x in reduce(lambda acc, y: acc + y[1], ALTERNATE_NAMES, [])]
    alts = [f for f in parties_votes if f[0].upper() in all_alternate_names]
    originals = [f for f in parties_votes if f[0].upper() not in all_alternate_names]

    return [merge_alt_names(ALTERNATE_NAMES, alts, x) for x in originals]


def maybe_update_name(name, alt_names):
    """Update a name to its canonical form if it has one.
    >>> maybe_update_name('DIE LINKE.', ALTERNATE_NAMES)
    'DIE LINKE'
    >>> maybe_update_name('GRÜNE/B 90', ALTERNATE_NAMES)
    'GRÜNE'
    """
    for names in alt_names:
        if name in names[1]:
            return names[0]
    return name


def update_alt_names(votes):
    """Update alternate names to their canonical forms."""
    return [(maybe_update_name(x[0], ALTERNATE_NAMES), x[1]) for x in votes]


def constituency_votes(constituency, values, vote_index):
    """Return the votes for the parties in a constituency."""
    const_vals = values_by(values, constituency, 1)
    votes = [(x[2], x[vote_index]) for x in const_vals]
    merged_votes = update_alt_names(votes)

    return merged_votes


def constituencies_votes(state, values, vote_index):
    """Return the votes for the constituencies in a state."""
    state_vals = values_by(values, state, 0)
    constituencies = unique_values_at(state_vals, 1)

    return [(x, constituency_votes(x, state_vals, vote_index)) for x in constituencies]


def cleanse_votes_by_constituencies(values, vote_index):
    """Return a list of states, where each state is a tuple of its id & a list of its constituents,
         & each constituent is a tuple of its id & a list of tuples of its parties & their votes.
         E.G.:
         [[('1',
               [('11',
                 [('CDU', 5000), ('DIE LINKE', 4000)]),
                ('15',
                 [('DIE LINKE', 6000), ('CDU', 3435)])])]]
         """

    real_parties = filter_not_parties(values)
    with_votes = [x for x in real_parties if x[vote_index] != '-']
    states = unique_values_at(values, 0)
    states_votes = [(x, constituencies_votes(x, with_votes, vote_index)) for x in states]

    return states_votes


def cleanse_first_votes_by_constituencies(values):
    return cleanse_votes_by_constituencies(values, 3)


def first_votes_by_constituencies():
    """Return parties in all states and constituencies with their 1st votes."""
    return cleanse_first_votes_by_constituencies(CSV_VOTES)


def higher(first, second):
    """Return the tuple with a higher value in its 2nd element among 2 tuples.
    >>> higher(('foo', 45), ('bar', 65))
    ('bar', 65)
    >>> higher(('baz', 54), ('baz', 43))
    ('baz', 54)
    >>> higher(('lore', 43), ('role', 43))
    ('lore', 43)
    """
    if int(second[1]) > int(first[1]):
        return second
    return first


def constituency_winner(parties):
    """Return the winner of a constituency."""
    return reduce(lambda acc, x: higher(acc, x), parties, ('', 0))


def state_constituency_winners(state_vals):
    """Return the winners of the constituencies in a state."""
    return [(y[0], constituency_winner(y[1])[0]) for y in state_vals]


def direct_seat_winners():
    """Return the winners of the direktmandat for all constituencies in all states.
    E.g. [('1', [('11', 'CDU'), ('4', 'DIE LINKE')]), ('2', [('1', 'CDU'), ('10', 'SPD')])]"""
    votes = first_votes_by_constituencies()

    return [(x[0], state_constituency_winners(x[1])) for x in votes]


def wins_per_party(constituency_winners):
    """Return the number of constituencies won by each party."""
    parties = [x[1] for x in constituency_winners]
    return list(Counter(parties).items())


def states_direct_seats():
    """Return each party's share of wins in each state."""
    winners = direct_seat_winners()
    return [(x[0], wins_per_party(x[1])) for x in winners]


def cleanse_second_votes(dirty):
    """Return a sorted list of merged parties with non-zero votes."""
    unsorted_votes = list(map(aggregate, map(filter_dashes, dirty)))
    non_zero_votes = [x for x in unsorted_votes if x[1] != 0]
    merged_votes = merge_parties_alt_names(non_zero_votes)

    return sorted(merged_votes, key=lambda tup: tup[1], reverse=True)


def percentage(num, denom):
    """Return the percentage string of a numerator to its denominator."""
    return '{:.4f}'.format((float(num) / denom) * 100)


def votes_with_percentages(votes):
    """Return a list of parties along with their votes and percentage share.
    >>> votes_with_percentages(([('CDU', 500), ('SPD', 600), ('MLPD', 50)]))
    [('CDU', '43.4783'), ('SPD', '52.1739'), ('MLPD', '4.3478')]
    """
    vote_total = total(votes)
    return [(kv[0], percentage(kv[1], vote_total)) for kv in votes]


def second_votes():
    """Compute zweitstimmen for all parties in the csv."""
    # party names are at the 3rd index.
    parties = unique_values_at(CSV_VOTES, 2)

    # contains `-` values for missing votes.
    unfiltered_values = [(x, party_second_votes(x, CSV_VOTES)) for x in parties]

    return cleanse_second_votes(unfiltered_values)


def cleanse_second_votes_by_constituencies(values):
    return cleanse_votes_by_constituencies(values, 4)


def second_votes_by_constituencies():
    """Return parties in all states and constituencies with their 2nd votes."""
    return cleanse_second_votes_by_constituencies(CSV_VOTES)


def add_if_party_matches(party, acc, party_votes):
    """Add vote if owner matches a party."""
    if party == party_votes[0]:
        return acc + int(party_votes[1])

    return acc


def second_vote_by_state(state_votes):
    """Return parties in a states with their 2nd votes."""
    votes = flatten([x[1] for x in state_votes])
    parties = set([x[0] for x in votes])

    return [(x, reduce(lambda acc, y: add_if_party_matches(x, acc, y), votes, 0)) for x in parties]


def second_votes_by_states():
    """Return parties in all states with their 2nd votes."""
    votes = second_votes_by_constituencies()

    return [(x[0], second_vote_by_state(x[1])) for x in votes]


def total_below(vs, percent):
    """Return total votes below the provided percentage."""
    other_votes = [x for x in vs if float(x[1]) < percent]
    return reduce(lambda acc, kv: acc + float(kv[1]), other_votes, 0)


def chart():
    """Display a bar chart of parties to the percentages of their votes."""
    vs = votes_with_percentages(second_votes())

    above_five_percent = list(filter(lambda x: float(x[1]) >= 5, vs))
    total_below_5_percent = total_below(vs, 5)
    above_five_percent.extend([('others', total_below_5_percent)])

    percents = list(map(lambda x: float(x[1]), above_five_percent))
    party_titles = list(map(lambda x: x[0], above_five_percent))
    x_pos = range(len(party_titles))

    colors = 'rgbkymc'

    plt.bar(x_pos, percents, align='center', color=colors, alpha=0.6)
    plt.xticks(x_pos, party_titles)
    plt.ylabel('Percentages of Votes Won')
    plt.xlabel('Parties')
    plt.title('2017 German Election Results')

    plt.show()


def chart_with_labels():
    """Display a bar chart with percentage labels for each bar."""
    vs = votes_with_percentages(second_votes())

    above_five_percent = list(filter(lambda x: float(x[1]) >= 5, vs))
    total_below_5_percent = total_below(vs, 5)
    above_five_percent.extend([('others', total_below_5_percent)])

    percents = list(map(lambda x: float(x[1]), above_five_percent))
    party_titles = list(map(lambda x: x[0], above_five_percent))

    freq_series = pd.Series.from_array(percents)

    plt.figure(figsize=(12, 8))
    ax = freq_series.plot(kind='bar')
    ax.set_title('2017 German Election Results')
    ax.set_xlabel('Parties')
    ax.set_ylabel('Percentages of Votes Won')
    ax.set_xticklabels(party_titles)

    bars = ax.patches

    labels = ['{:.2f}'.format(percents[i]) + '%' for i in range(len(percents))]

    for rect, label in zip(bars, labels):
        height = rect.get_height()
        ax.text(rect.get_x() + rect.get_width() / 2, height, label, ha='center', va='bottom')

    plt.show()


def write_second_votes():
    """Display semicolon separated values of parties and their vote percentages."""
    with open('_second_votes.csv', 'w') as csv_file:
        csv_file.write('Party;Votes\n')
        for v in second_votes():
            csv_file.write('{0};{1}\n'.format(v[0], v[1]))


def compute_seat_distribution(total_seats, proportions):
    """Seat allocation algorithm."""
    total_proportions = total(proportions)
    divisor = round(float(total_proportions) / total_seats)

    while True:
        trial_seats = [(x[0], round(float(x[1]) / divisor)) for x in proportions]
        total_trial_seats = total(trial_seats)

        if total_trial_seats != total_seats:
            if total_trial_seats > total_seats:
                divisor += 1
            else:
                divisor -= 1
            continue

        return trial_seats


def state_seat_distribution():
    """Allocate seats to each state based on population."""
    total_seats = 598
    state_pops = [(x[0], x[2]) for x in CSV_POPULATION]

    return compute_seat_distribution(total_seats, state_pops)


def compute_state_seats(state_votes, state_distribution, parties):
    """Compute no. of seats for parties in each state."""
    state, parties_votes = state_votes
    eligible_votes = [x for x in parties_votes if x[0] in parties]
    _, total_seats = lookup_1st_value(state_distribution, state)

    return compute_seat_distribution(total_seats, eligible_votes)


def eligible_parties(votes):
    """Return parties that have more than 5% of the total votes.
    >>> eligible_parties([('CDU', 100), ('SPD', 50), ('MLPD', 5)])
    ['CDU', 'SPD']
    """
    percentages = votes_with_percentages(votes)
    above_five_percent = [x for x in percentages if float(x[1]) >= 5]
    return list(map(lambda x: x[0], above_five_percent))


def second_vote_seat_distribution():
    """Allocate seats based on zweitstimmen."""
    parties = eligible_parties(second_votes())
    votes_by_state = second_votes_by_states()
    state_distribution = state_seat_distribution()

    return list(
        map(lambda x: (x[0], compute_state_seats(x, state_distribution, parties)), votes_by_state))


def write_direct_and_list_seats():
    """Print the seat distribution in the Bundestag."""
    first = states_direct_seats()
    second = second_vote_seat_distribution()

    with open('_direct_list_seats.csv', 'w') as csv_file:
        csv_file.write('state;party;direct_seats;list_seats;ueberhang\n')
        for i in second:
            for j in first:
                # if it's the same state
                if i[0] == j[0]:
                    for k in i[1]:
                        first_vote_counterpart = lookup_1st_value(j[1], k[0])
                        state_name = lookup_state_name(i[0])

                        # Ueberhang is the difference when direct seats are more than list seats.
                        ueberhang = int(first_vote_counterpart[1]) - int(k[1])
                        if ueberhang < 0:
                            ueberhang = 0

                        csv_file.write(
                            '{0};{1};{2};{3};{4}\n'.format(state_name, k[0], first_vote_counterpart[1], k[1],
                                                           ueberhang))


def sum_party_across_states(values, party):
    """Sum up values for a party across states.
    >>> example = \
    [('14', \
      [('MLPD', 2566), \
       ('SPD', 261105), \
       ('BGE', 9451)]), \
     ('10', \
      [('MLPD', 427), \
       ('SPD', 158895), \
       ('BGE', 1025)])]
    >>> sum_party_across_states(example, 'SPD')
    ('SPD', 420000)
    """
    party_across_states = list(map(lambda state: list(filter(lambda y: y[0] == party, state[1])), values))
    return party, total(flatten(party_across_states))


def lookup_party_across_states(values, party):
    """Sum up values for a party across states.
    >>> example = \
    [('14', \
      [('MLPD', 2566), \
       ('SPD', 261105), \
       ('BGE', 9451)]), \
     ('10', \
      [('MLPD', 427), \
       ('SPD', 158895), \
       ('BGE', 1025)])]
    >>> lookup_party_across_states(example, 'SPD')
    [('14', ['SPD', 261105]), ('10', ['SPD', 158895])]
    """
    return list(map(lambda state: (state[0], flatten(list(filter(lambda y: y[0] == party, state[1])))), values))


def lookup_party_in_state(values, state, party):
    """Look up a party in a particular state.
    >>> example = \
    [('1', [('CDU', 10), ('SPD', 1)]), \
     ('3', [('CDU', 16), ('SPD', 14)]), \
     ('12', [('CDU', 9), ('SPD', 1)])]
    >>> lookup_party_in_state(example, '3', 'CDU')
    ('CDU', 16)
    >>> lookup_party_in_state([], '419', 'FRAUD')
    ('FRAUD', 0)
    """
    party_across_states = flatten(
        list(map(lambda s: list(filter(lambda y: (s[0] == state) & (y[0] == party), s[1])), values)))

    if not party_across_states:
        return party, 0

    return party_across_states[0]


def compute_mindessitzzahl(first_seats, second_seats):
    """Compute the Mindessitzzahl for each party in each state.
    >>> first_seats = \
    [(3, \
        [('CDU', 3), \
        ('SPD', 24)]), \
    (11, \
        [('CDU', 7), \
        ('SPD', 9)])]
    >>> second_seats = \
    [(3, \
        [('CDU', 11), \
        ('SPD', 12), \
        ('MLPD', 3)]), \
    (11, \
        [('CDU', 4), \
        ('SPD', 60)]), \
    (13, \
        [('CDU', 1), \
        ('SPD', 40)])]
    >>> compute_mindessitzzahl(first_seats, second_seats)
    [(3, [('CDU', 11), ('SPD', 24), ('MLPD', 3)]), (11, [('CDU', 7), ('SPD', 60)]), (13, [('CDU', 1), ('SPD', 40)])]
    """
    return list(map(lambda x:
                    (x[0], list(map(lambda y:
                                    higher(y, lookup_party_in_state(first_seats, x[0], y[0])), x[1]))), second_seats))


def federal_mindessitzzahl(parties, mindessitzzahl):
    """Return the Mindessitzzahl for each party across all states.
    >>> mindessitzzahl = \
   [(3, [('CDU', 11), ('SPD', 24), ('MLPD', 3)]), (11, [('CDU', 7), ('SPD', 60)]), (13, [('CDU', 1), ('SPD', 40)])]
    >>> federal_mindessitzzahl(['CDU', 'SPD'],  mindessitzzahl)
    [('CDU', 19), ('SPD', 124)]
    """
    return [sum_party_across_states(mindessitzzahl, x) for x in parties]


def is_mindessitzzahl_reached(distribution, mindessitzzahl):
    """Has each party reached its mindessitzzahl?
    >>> distribution = [('CDU', 40), ('SPD', 36), ('CSU', 15)]
    >>> mindessitzzahl = [('CDU', 43), ('SPD', 33), ('CSU', 20)]
    >>> is_mindessitzzahl_reached(distribution, mindessitzzahl)
    False

    >>> distribution = [('CDU', 43), ('SPD', 36), ('CSU', 21)]
    >>> mindessitzzahl = [('CDU', 43), ('SPD', 33), ('CSU', 20)]
    >>> is_mindessitzzahl_reached(distribution, mindessitzzahl)
    True
    """
    return reduce(lambda acc, x: acc & (x[1] >= lookup_1st_value(mindessitzzahl, x[0])[1]), distribution, True)


def compute_mindessitzzahl_distribution(total_seats, proportions, mindessitzzahl):
    """Seat allocation algorithm based on mindessitzzahl."""
    total_proportions = total(proportions)

    while True:
        divisor = round(float(total_proportions) / total_seats)
        trial_seats = [(x[0], round(float(x[1]) / divisor)) for x in proportions]
        if not is_mindessitzzahl_reached(trial_seats, mindessitzzahl):
            total_seats += 1
            continue

        return trial_seats


def federal_seat_distribution():
    """Return the seat distribution for all parties at the federal level."""
    votes = second_votes()
    parties = eligible_parties(votes)
    eligible_votes = [x for x in votes if x[0] in parties]

    first_seats = states_direct_seats()
    second_seats = second_vote_seat_distribution()

    mindessitzzahl = compute_mindessitzzahl(first_seats, second_seats)
    federal_mindessitz = federal_mindessitzzahl(parties, mindessitzzahl)
    total_seats = total(federal_mindessitz)

    return compute_mindessitzzahl_distribution(total_seats, eligible_votes, federal_mindessitz)


def resolve_direkt_mandat_minimum(direkt_mandat_seats, state_seat, party):
    """Each state list must receive at least as many seats as the number
     of constituencies which the party won in the Land in question.
     >>> resolve_direkt_mandat_minimum([('2', [('SPD', 5), ('CDU', 1)])], ('2', 4), 'SPD')
     ('2', 5)
     >>> resolve_direkt_mandat_minimum([('4', [('SPD', 7), ('CDU', 6)])], ('4', 4), 'CDU')
     ('4', 6)
     """
    _, direkt_seat = lookup_party_in_state(direkt_mandat_seats, state_seat[0], party)

    if direkt_seat > state_seat[1]:
        return state_seat[0], direkt_seat

    return state_seat


def party_seat_distribution(direct_seats, federal_seats, party, votes):
    """Distribute the seats for a party in its states potentially going over the allocated number of seats."""
    party_across_states = lookup_party_across_states(votes, party)
    cleansed_states = [x for x in party_across_states if len(x[1]) != 0]
    party_votes = [('{0}__{1}'.format(x[0], x[1][0]), x[1][1]) for x in cleansed_states]
    _, total_seats = lookup_1st_value(federal_seats, party)

    distribution = [(x[0].split('__')[0], x[1]) for x in compute_seat_distribution(total_seats, party_votes)]

    return [resolve_direkt_mandat_minimum(direct_seats, x, party) for x in distribution]


def second_smallest(numbers):
    """Find the second smallest number. Lifted from Stack Overflow.
    >>> second_smallest([2, 32, -0.23, 0.11, 1])
    0.11
    >>> second_smallest([31, 17, 17])
    17
    >>> second_smallest([])
    inf
    """
    m1, m2 = float('inf'), float('inf')
    for x in numbers:
        if x <= m1:
            m1, m2 = x, m1
        elif x < m2:
            m2 = x
    return m2


def custom_round(x, base=10):
    """Custom rounding function."""
    return int(base * round(float(x) / base))


def correct_over_allocation(direct_seats, federal_seats, party, votes, party_list_seats):
    """Correct over-allocation of a party's seats in its states."""
    party_seats = lookup_1st_value(federal_seats, party)[1]
    _, total_votes = sum_party_across_states(votes, party)
    divisor = total_votes / party_seats
    lookup_votes = lookup_party_in_state

    while True:
        # candidate seats are derived from dividing the second votes of each state by the starting divisor.
        initial_candidates = [(x[0], round(lookup_votes(votes, x[0], party)[1] / divisor)) for x in party_list_seats]

        # no. of seats allocated to each state should be the max between its direct seats and list seats.
        candidates_seats = [(x[0], max(x[1], lookup_votes(direct_seats, x[0], party)[1])) for x in initial_candidates]

        if total(candidates_seats) != party_seats:
            # 2 sets of divisor candidates are derived based on the previous candidate seats distribution.
            # candidate divisors that would result in a divisor smaller than the previous divisor are discarded.
            divisor_candidates_1 = [(lookup_votes(votes, x[0], party)[1] / (x[1] - 0.5)) for x in candidates_seats
                                    if lookup_votes(votes, x[0], party)[1] > divisor]
            divisor_candidates_2 = [(lookup_votes(votes, x[0], party)[1] / (x[1] - 1.5)) for x in candidates_seats
                                    if lookup_votes(votes, x[0], party)[1] > divisor]

            divisor_candidates = [x for x in divisor_candidates_1 if x > divisor] + \
                                 [x for x in divisor_candidates_2 if x > divisor]

            divisor_candidate_1 = min(divisor_candidates)
            divisor_candidate_2 = second_smallest(divisor_candidates)
            diff = divisor_candidate_2 - divisor_candidate_1

            divisor = custom_round(divisor_candidate_2 - (diff / 2), (diff / 2))
            continue

        return [(lookup_state_name(x[0]), x[1]) for x in candidates_seats]


def parties_seat_distributions():
    """Distribute the seats for each party in its states and correct over-allocations."""
    votes_by_state = second_votes_by_states()
    federal_seats = federal_seat_distribution()
    parties = [x[0] for x in federal_seats]
    direct_seats = states_direct_seats()

    initial_distribution = [(x, party_seat_distribution(direct_seats, federal_seats, x, votes_by_state))
                            for x in parties]
    return [(x[0], correct_over_allocation(direct_seats, federal_seats, x[0], votes_by_state, x[1]))
            for x in initial_distribution]


def write_parties_seat_distributions():
    with open('_bundestag_seats.csv', 'w') as csv_file:
        csv_file.write('Party;State;Seats\n')
        for p in parties_seat_distributions():
            for s in p[1]:
                csv_file.write('{0};{1};{2}\n'.format(p[0], s[0], s[1]))


def state_urls():
    """Returns html pages containing the election results of each state."""
    root_url = 'https://www.bundeswahlleiter.de/en/bundestagswahlen/2017/'
    root_page = BeautifulSoup(requests.get(root_url + 'wahlbewerber.html').content, 'html5lib')

    urls = root_page.body.findAll('a', href=re.compile('wahlbewerber/bund-99/land-'))
    return set(['{0}{1}'.format(root_url, l['href']) for l in urls])


def pages(urls):
    """Fetch web pages."""
    return [[url, BeautifulSoup(requests.get(url).content, 'html5lib')] for url in urls]


def get_urls(root_url, urls):
    """Extracts the urls from a list of BeautifulSoup anchors(<a>) and joins each with the root url.
    >>> expected = {'http://example.com/secret/1.html', 'http://example.com/secret/2.html'}
    >>> tags = \
    [BeautifulSoup('<a href="secret/1.html"></a>', "html5lib"), \
    BeautifulSoup('<a href="secret/2.html"></a>', "html5lib"), \
    BeautifulSoup('<a href="secret/1.html"></a>', "html5lib")]
    >>> get_urls('http://example.com/secret.html', [tag.a for tag in tags]) == expected
    True
    """
    return set(['{0}/{1}'.format(''.join(root_url.rsplit('/', 1)[:-1]), l['href']) for l in urls])


def constituency_urls(state_pages):
    """Returns the urls of constituencies for each state."""
    links = [[page[0], page[1].body.findAll('a', href=re.compile('^' + page[0].rsplit('/', 1)[-1].split('.')[0]))] for
             page in state_pages]
    return [get_urls(u[0], u[1]) for u in links]


def url_segment(url):
    """Returns a segment of a URL.
    >>> url_segment('https://www.bd.de/en/d/2017/wahlbewerber/bund-99/land-56.html',)
    '56'
    >>> url_segment('https://www.bd.de/en/d/2017/wahlbewerber/bund-99/land-76.html')
    '76'
    """
    return url.rsplit('/')[-1].split('-')[1].split('.')[0]


def get_constituency_candidate(row):
    party = row.find('th').get_text().strip()
    candidate_name = row.find('td').get_text().strip()
    return party, candidate_name


def get_constituency_candidates(url):
    page = BeautifulSoup(requests.get(url).content, 'html5lib')
    table = page.body.find('tbody')
    rows = table.findAll('tr')
    return [get_constituency_candidate(r) for r in rows]


def get_constituencies_candidates(constituencies):
    return [(u[0], get_constituency_candidates(u[1])) for u in constituencies]


def state_constituencies_candidates(urls):
    constituencies = [(url_segment(u), u) for u in urls]
    return get_constituencies_candidates(constituencies)


def states_constituencies_candidates(state_pages):
    """Return the candidates for direct seats in all states' constituencies."""
    return [state_constituencies_candidates(list(u)) for u in constituency_urls(state_pages)]


def direct_candidate(winning_party, candidates):
    """Return the elected candidate based on the party that won in a constituency.
    >>> direct_candidate(('190', 'CDU'), [('190', [('CDU', 'Selle, Johannes'), ('SPD', 'Matschie, Christoph')])])
    ('CDU', 'Selle, Johannes')
    """
    constituencies = lookup_1st_value(candidates, winning_party[0])
    candidate = lookup_1st_value(constituencies[1], winning_party[1])
    return candidate


def party_list_candidates(article):
    """Return the list candidates for a party."""
    table = article.find('tbody')
    party = article.find('span').get_text().strip()
    rows = table.findAll('tr')
    candidates = [r.findAll('td')[0].get_text().strip() for r in rows]
    return party, candidates


def state_list_candidates(page):
    """Return the list candidates from a state's web page."""
    return [party_list_candidates(a) for a in page.body.findAll('article')]


def count_entries(tuples, value):
    """Count the number of entries in a list of tuples that has the specified value.
    >>> count_entries([\
    ('CDU', 'Hirte, Christian'),\
    ('CDU', 'Selle, Johannes'),\
    ('SPD', 'Weiler, Albert Helmut')\
    ], 'SPD')
    1
    >>> count_entries([], 'CDU')
    0
    """
    return len([t for t in tuples if t[0] == value])


def fill_vacant_seats(state, party_state_distribution, direct_elects, candidates):
    # Go through the states the party has allotted seats.
    elected = direct_elects
    party, seats_across_states = party_state_distribution
    for s in seats_across_states:
        if s[0] == lookup_state_name(state):
            # check if the state already has enough elected candidates
            elected_diff = s[1] - count_entries(elected, party)
            if elected_diff > 0:
                # add to the elected candidates from the candidates list
                _, party_candidates = lookup_1st_value(candidates, party)
                if party_candidates == 0:
                    for alt_name in lookup_alt_names(ALTERNATE_NAMES, (party, 0)):
                        _, party_candidates = lookup_1st_value(candidates, alt_name)
                        if party_candidates != 0:
                            break

                already_elected = [m[1] for m in elected]
                valid_candidates = [m for m in party_candidates if m not in already_elected]
                newly_elected = [(party, m) for m in valid_candidates[0:elected_diff]]
                elected += newly_elected
    return elected


def state_elected_candidates(url, state_pages, constituencies_candidates):
    """Return candidates elected to the Bundestag in a particular state."""
    state = url_segment(url)
    _, page = lookup_1st_value(state_pages, url)
    list_candidates = state_list_candidates(page)
    const_direct_winners = lookup_1st_value(direct_seat_winners(), state)
    direct_elects = [direct_candidate(c, constituencies_candidates) for c in
                     const_direct_winners[1]]
    elected = [fill_vacant_seats(state, party_states, direct_elects, list_candidates) for party_states in
               parties_seat_distributions()]
    return lookup_state_name(state), elected[-1]


def elected_candidates():
    """Return the candidates elected to the Bundestag."""
    urls = state_urls()
    state_pages = pages(urls)
    constituencies_candidates = flatten(states_constituencies_candidates(state_pages))
    return [state_elected_candidates(url, state_pages, constituencies_candidates) for url in urls]


def directly_elected_candidates(url, constituencies_candidates):
    """Return candidates elected to the Bundestag from constituencies in a state."""
    state = url_segment(url)
    const_direct_winners = lookup_1st_value(direct_seat_winners(), state)
    direct_elects = [(c[0], direct_candidate(c, constituencies_candidates)[1]) for c in
                     const_direct_winners[1]]
    return direct_elects


def constituencies_elected_candidates():
    """Return candidates elected to the Bundestag in all constituencies across Germany."""
    urls = state_urls()
    state_pages = pages(urls)
    constituencies_candidates = flatten(states_constituencies_candidates(state_pages))
    return flatten([directly_elected_candidates(url, constituencies_candidates) for url in urls])


def write_elected_candidates():
    with open('_elected_candidates.csv', 'w') as csv_file:
        csv_file.write('State;Party;Candidate\n')
        for s in elected_candidates():
            for m in s[1]:
                csv_file.write('{0};{1};{2}\n'.format(s[0], m[0], m[1]))


def directly_elected_candidates_map():
    shp_file = 'Geometrie_Wahlkreise_19DBT_geo'
    shp_file_dir = 'shapefiles/'
    sf = shapefile.Reader(shp_file_dir + shp_file)

    election_map = Basemap(llcrnrlon=5.87, llcrnrlat=47.27, urcrnrlon=15.04, urcrnrlat=55.06,
                           resolution='i', projection='tmerc', lat_0=51.16, lon_0=10.45)

    election_map.drawmapboundary(fill_color='aqua')
    election_map.fillcontinents(color='#ddaa66', lake_color='aqua')
    election_map.drawcoastlines()

    election_map.readshapefile(shp_file_dir + shp_file, shp_file)

    candidates = constituencies_elected_candidates()

    for shapeRec in sf.shapeRecords():
        x_lon = np.zeros((len(shapeRec.shape.points), 1))
        y_lat = np.zeros((len(shapeRec.shape.points), 1))
        for ip in range(len(shapeRec.shape.points)):
            x_lon[ip] = shapeRec.shape.points[ip][0]
            y_lat[ip] = shapeRec.shape.points[ip][1]

        mid_x_lon = (min(x_lon) + max(x_lon)) / 2
        mid_y_lat = (min(y_lat) + max(y_lat)) / 2
        x, y = election_map(mid_x_lon, mid_y_lat)

        constituency = shapeRec.record[0]
        _, candidate = lookup_1st_value(candidates, str(constituency))

        plt.text(x, y, candidate, fontsize=10, ha='center', va='center')

    plt.show()


funcs = {
    'bundestag_seats': write_parties_seat_distributions,
    'elected_candidates': write_elected_candidates,
    'direct_list_seats': write_direct_and_list_seats,
    'second_votes': write_second_votes,
    'second_votes_chart': chart_with_labels,
    'directly_elected_candidates_map': directly_elected_candidates_map
}

if len(sys.argv) > 1:
    if sys.argv[1] in funcs:
        funcs[sys.argv[1]]()
    else:
        print("Output %s not supported" % sys.argv[1])

if __name__ == '__main__':
    import doctest

    doctest.testmod()
