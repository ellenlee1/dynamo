import logging
import collections

logger = logging.getLogger(__name__)

class Policy(object):
    """
    Responsible for partitioning the replicas, setting quotas and activating deletion on sites, and making deletion decisions on replicas.
    The core of the object is a stack of rules (specific rules first) with a fall-back default decision.
    A rule is a callable object with (replica, demand_manager) as arguments that returns None or (replica, decision, reason)
    """

    # do not change order - used by history records
    DEC_DELETE, DEC_KEEP, DEC_PROTECT = range(1, 4)
    DECISION_STR = {DEC_DELETE: 'DELETE', DEC_KEEP: 'KEEP', DEC_PROTECT: 'PROTECT'}

    def __init__(self, default, rules, quotas, partition = '', site_requirement = None, prerequisite = None):
        self.default_decision = default # decision
        self.rules = rules # [rule]
        self.quotas = quotas # {site: quota}
        self.partition = partition
        self.groups = [] # groups the policy concerns
        self.site_requirement = site_requirement # bool(site, partition, initial). initial: check deletion should be triggered

        # An object with two methods dataset(replica) and block(replica)
        # dataset: int(replica), 0: does not apply, 1: applies on all blocks, 2: partially applies
        # block: bool(replica)
        self.prerequisite = prerequisite

    def applies(self, replica): # check if dataset replica is in the partition
        if self.prerequisite is None:
            return 1
        else:
            return self.prerequisite.dataset(replica)

    def block_applies(self, replica): # check if block replica is in the partition
        if self.prerequisite is None:
            return True
        else:
            return self.prerequisite.block(replica)

    def need_deletion(self, site, initial = False):
        if self.site_requirement is None:
            return True
        else:
            return self.site_requirement(site, self.partition, initial)

    def evaluate(self, replica, demand_manager):
        for rule in self.rules:
            result = rule(replica, demand_manager)
            if result is not None:
                break
        else:
            return replica, self.default_decision, 'Policy default'

        return result

    def sort_deletion_candidates(self, replicas, demands):
        """
        Rank and sort replicas in decreasing order of deletion priority.
        """

        return sorted(replicas, key = lambda r: demands.dataset_demands[r.dataset].global_usage_rank, reverse = True)
