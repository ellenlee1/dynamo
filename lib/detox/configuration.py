from common.dataformat import Configuration

main = Configuration(
    activity_indicator = '/home/cmsprod/public_html/IntelROCCS/Detox/inActionLock.txt',
    deletion_per_iteration = 0, # fraction of quota to delete per iteration
    deletion_volume_per_request = 50, # size to delete per deletion request in TB
    exclude_if_on = [], # if a dataset has a replica on these [sites], don't consider it for deletions. Introduced because of CNAF indicent.
    time_shift = 0. # number of days in the future from which to evaluate the policies
)
