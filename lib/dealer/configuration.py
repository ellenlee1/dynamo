import common.configuration as common

included_sites = ['T2_*', 'T1_*_Disk']

demand_refresh_interval = 7200. # update demand if demand manager time_today is more than 7200 seconds ago

max_dataset_size = 50. # Maximum dataset size to consider for copy in TB

request_to_replica_threshold = 1.75 # (weighted number of requests) / (number of replicas) above which replication happens

max_copy_per_site = 50. # Maximum volume to be copied per site in TB
max_copy_total = 200.

max_replicas = 10

target_site_occupancy = 0.9

overflow_factor = 1.01 # Potentially copy up to target occupancy * overflow_factor

summary_html = '/home/cmsprod/public_html/dynamo/dealer/copy_decisions.html'

balancer_target_reasons = [
    'dataset.name == /*/*/MINIAOD* and replica.num_full_disk_copy_common_owner < 3',
    'replica.num_full_disk_copy_common_owner < 2'
]
