class CopyInterface(object):
    """
    Interface to data copy application.
    """

    def __init__(self):
        pass

    def schedule_copy(self, dataset_replica, origin = None, comments = ''):
        """
        Schedule and execute a copy operation. Argument origin can be None for copy interfaces
        that do not require the origin to be specified.
        Returns the operation id.
        """

        return 0

    def schedule_copies(self, replica_origin_list, comments = ''):
        """
        Schedule mass copies. Subclasses can implement efficient algorithms.
        Returns {operation id: (approved, [(replica, origin)])}
        """

        request_mapping = {}
        for replica, origin in replica_origin_list:
            operation_id = self.schedule_copy(replica, origin, comments)
            request_mapping[operation_id] = (True, [(replica, origin)])

        return request_mapping

    def copy_status(self, operation_id):
        """
        Returns the completion status specified by the operation id as a
        {dataset: (last_update, total, copied)} dictionary.
        """

        return {}
