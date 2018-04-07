import time
import logging
import fnmatch

from dynamo.core.persistency.base import InventoryStore
from dynamo.utils.interface.mysql import MySQL
from dynamo.dataformat import Dataset, Block, File, Site, SitePartition, Group, DatasetReplica, BlockReplica

LOG = logging.getLogger(__name__)

class MySQLInventoryStore(InventoryStore):
    """InventoryStore with a MySQL backend."""

    def __init__(self, config):
        InventoryStore.__init__(self, config)

        self._mysql = MySQL(config.db_params)

        # Because updates often happen for the same datasets/blocks
        # We can do something smarter at some point (e.g. automatically consolidate update calls)
        self._dataset_id_cache = ('', 0)
        self._block_id_cache = ('', 0, 0)
        self._site_id_cache = ('', 0)
        self._group_id_cache = ('', 0)
        self._partition_id_cache = ('', 0)

    def close(self):
        self._mysql.close()

    def check_connection(self): #override
        try:
            result = self._mysql.query('SELECT * FROM `partitions`')
        except:
            return False

        return True

    def get_partition_names(self):
        return self._mysql.query('SELECT `name` FROM `partitions`')

    def get_group_names(self, include = ['*'], exclude = []): #override
        # Load groups
        group_names = []

        names = self._mysql.xquery('SELECT `name` FROM `groups`')

        for name in names:
            for filt in include:
                if fnmatch.fnmatch(name, filt):
                    break
            else:
                # no match
                continue

            for filt in exclude:
                if fnmatch.fnmatch(name, filt):
                    break
            else:
                # no match
                group_names.append(name)

        return group_names

    def get_site_names(self, include = ['*'], exclude = []): #override
        # Load sites
        site_names = []

        names = self._mysql.xquery('SELECT `name` FROM `sites`')

        for name in names:
            for filt in include:
                if fnmatch.fnmatch(name, filt):
                    break
            else:
                # no match
                continue

            for filt in exclude:
                if fnmatch.fnmatch(name, filt):
                    break
            else:
                # no match
                site_names.append(name)

        return site_names

    def get_dataset_names(self, include = ['*'], exclude = []): #override
        dataset_names = []

        include_patterns = []
        for pattern in include:
            sql = 'SELECT `name` FROM `datasets` WHERE `name` LIKE %s'
            names = self._mysql.xquery(sql, pattern.replace('*', '%').replace('?', '_'))
    
            for name in names:
                for filt in exclude:
                    if fnmatch.fnmatch(name, filt):
                        break
                else:
                    # no match
                    dataset_names.append(name)

        return dataset_names

    def get_files(self, block):
        if LOG.getEffectiveLevel() == logging.DEBUG:
            LOG.debug('Loading files for block %s', block.full_name())

        files = set()

        if block.id == 0:
            return files

        # assuming unique block names
        sql = 'SELECT `id`, `size`, `name` FROM `files` WHERE `block_id` = %s'

        for fid, size, name in self._mysql.xquery(sql, block.id):
            files.add(File(name, block, size, fid))

        return files

    def load_data(self, inventory, group_names = None, site_names = None, dataset_names = None): #override
        ## We need the temporary tables to stay alive
        reuse_connection_orig = self._mysql.reuse_connection
        self._mysql.reuse_connection = True

        ## Load groups
        LOG.info('Loading groups.')

        if group_names is not None:
            # set up a temporary table to be joined with later queries
            groups_tmp = self._setup_constraints('groups', group_names)
        else:
            groups_tmp = None

        id_group_map = {0: inventory.groups[None]}
        self._load_groups(inventory, id_group_map, groups_tmp)

        LOG.info('Loaded %d groups.', len(inventory.groups))

        ## Load sites
        LOG.info('Loading sites.')

        if site_names is not None:
            # set up a temporary table to be joined with later queries
            sites_tmp = self._setup_constraints('sites', site_names)
        else:
            sites_tmp = None

        id_site_map = {}
        self._load_sites(inventory, id_site_map, sites_tmp)

        LOG.info('Loaded %d sites.', len(inventory.sites))

        ## Load datasets
        LOG.info('Loading datasets.')
        start = time.time()

        if dataset_names is not None:
            # set up a temporary table to be joined with later queries
            datasets_tmp = self._setup_constraints('datasets', dataset_names)
        else:
            datasets_tmp = None

        id_dataset_map = {}
        self._load_datasets(inventory, id_dataset_map, datasets_tmp)

        LOG.info('Loaded %d datasets in %.1f seconds.', len(inventory.datasets), time.time() - start)

        ## Load blocks
        LOG.info('Loading blocks.')
        start = time.time()

        id_block_maps = {} # {dataset_id: {block_id: block}}
        self._load_blocks(inventory, id_dataset_map, id_block_maps, datasets_tmp)

        num_blocks = sum(len(m) for m in id_block_maps.itervalues())

        LOG.info('Loaded %d blocks in %.1f seconds.', num_blocks, time.time() - start)

        ## Load replicas (dataset and block in one go)
        LOG.info('Loading replicas.')
        start = time.time()

        self._load_replicas(
            inventory, id_group_map, id_site_map, id_dataset_map, id_block_maps,
            groups_tmp, sites_tmp, datasets_tmp
        )

        num_dataset_replicas = 0
        num_block_replicas = 0
        for dataset in id_dataset_map.itervalues():
            num_dataset_replicas += len(dataset.replicas)
            num_block_replicas += sum(len(r.block_replicas) for r in dataset.replicas)

        LOG.info('Loaded %d dataset replicas and %d block replicas in %.1f seconds.', num_dataset_replicas, num_block_replicas, time.time() - start)

        ## Cleanup
        if group_names is not None:
            self._mysql.drop_tmp_table('groups_load')
        if site_names is not None:
            self._mysql.drop_tmp_table('sites_load')
        if dataset_names is not None:
            self._mysql.drop_tmp_table('datasets_load')

        self._mysql.reuse_connection = reuse_connection_orig

    def _load_groups(self, inventory, id_group_map, groups_tmp):
        sql = 'SELECT g.`id`, g.`name`, g.`olevel` FROM `groups` AS g'

        if groups_tmp is not None:
            sql += ' INNER JOIN `%s`.`%s` AS t ON t.`id` = g.`id`' % groups_tmp

        for group_id, name, olname in self._mysql.xquery(sql):
            group = Group(
                name,
                olevel = Group.olevel_val(olname),
                gid = group_id
            )

            inventory.groups.add(group)
            id_group_map[group_id] = group

    def _load_sites(self, inventory, id_site_map, sites_tmp):
        sql = 'SELECT s.`id`, s.`name`, s.`host`, s.`storage_type`+0, s.`backend`, `status`+0 FROM `sites` AS s'

        if sites_tmp is not None:
            sql += ' INNER JOIN `%s`.`%s` AS t ON t.`id` = s.`id`' % sites_tmp

        for site_id, name, host, storage_type, backend, status in self._mysql.xquery(sql):
            site = Site(
                name,
                host = host,
                storage_type = storage_type,
                backend = backend,
                status = status,
                sid = site_id
            )

            inventory.sites.add(site)
            id_site_map[site_id] = site

            for partition in inventory.partitions.itervalues():
                site.partitions[partition] = SitePartition(site, partition)

        # Load site quotas
        sql = 'SELECT q.`site_id`, p.`name`, q.`storage` FROM `quotas` AS q INNER JOIN `partitions` AS p ON p.`id` = q.`partition_id`'

        if sites_tmp is not None:
            sql += ' INNER JOIN `%s`.`%s` AS t ON t.`id` = q.`site_id`' % sites_tmp

        for site_id, partition_name, storage in self._mysql.xquery(sql):
            try:
                site = id_site_map[site_id]
            except KeyError:
                continue

            partition = inventory.partitions[partition_name]
            site.partitions[partition].set_quota(int(storage * 1.e+12))

    def _load_datasets(self, inventory, id_dataset_map, datasets_tmp):
        sql = 'SELECT d.`id`, d.`name`, d.`size`, d.`num_files`, d.`status`+0, d.`data_type`+0, s.`cycle`, s.`major`, s.`minor`, s.`suffix`, UNIX_TIMESTAMP(d.`last_update`), d.`is_open`'
        sql += ' FROM `datasets` AS d'
        sql += ' LEFT JOIN `software_versions` AS s ON s.`id` = d.`software_version_id`'

        if datasets_tmp is not None:
            sql += ' INNER JOIN `%s`.`%s` AS t ON t.`id` = d.`id`' % datasets_tmp

        for dataset_id, name, size, num_files, status, data_type, sw_cycle, sw_major, sw_minor, sw_suffix, last_update, is_open in self._mysql.xquery(sql):
            # size and num_files are reset when loading blocks
            dataset = Dataset(
                name,
                size = size,
                num_files = num_files,
                status = int(status),
                data_type = int(data_type),
                last_update = last_update,
                is_open = (is_open == 1),
                did = dataset_id
            )
            if sw_cycle is None:
                dataset.software_version = None
            else:
                dataset.software_version = (sw_cycle, sw_major, sw_minor, sw_suffix)

            inventory.datasets[name] = dataset
            id_dataset_map[dataset_id] = dataset

    def _load_blocks(self, inventory, id_dataset_map, id_block_maps, datasets_tmp):
        sql = 'SELECT b.`id`, b.`dataset_id`, b.`name`, b.`size`, b.`num_files`, b.`is_open`, UNIX_TIMESTAMP(b.`last_update`) FROM `blocks` AS b'

        if datasets_tmp is not None:
            sql += ' INNER JOIN `%s`.`%s` AS t ON t.`id` = b.`dataset_id`' % datasets_tmp

        sql += ' ORDER BY b.`dataset_id`'

        _dataset_id = 0
        dataset = None
        for block_id, dataset_id, name, size, num_files, is_open, last_update in self._mysql.xquery(sql):
            if dataset_id != _dataset_id:
                _dataset_id = dataset_id

                dataset = id_dataset_map[dataset_id]
                dataset.blocks.clear()
                dataset.size = 0
                dataset.num_files = 0

                id_block_map = id_block_maps[dataset_id] = {}

            block = Block(
                Block.to_internal_name(name),
                dataset,
                size = size,
                num_files = num_files,
                is_open = (is_open == 1),
                last_update = last_update,
                bid = block_id
            )

            dataset.blocks.add(block)
            dataset.size += block.size
            dataset.num_files += block.num_files

            id_block_map[block_id] = block

    def _load_replicas(self, inventory, id_group_map, id_site_map, id_dataset_map, id_block_maps, groups_tmp, sites_tmp, datasets_tmp):
        sql = 'SELECT dr.`dataset_id`, dr.`site_id`,'
        sql += ' br.`block_id`, br.`group_id`, br.`is_complete`, br.`is_custodial`, brs.`size`, UNIX_TIMESTAMP(br.`last_update`)'
        sql += ' FROM `dataset_replicas` AS dr'
        sql += ' INNER JOIN `blocks` AS b ON b.`dataset_id` = dr.`dataset_id`'
        sql += ' LEFT JOIN `block_replicas` AS br ON (br.`block_id`, br.`site_id`) = (b.`id`, dr.`site_id`)'
        sql += ' LEFT JOIN `block_replica_sizes` AS brs ON (brs.`block_id`, brs.`site_id`) = (b.`id`, dr.`site_id`)'

        if groups_tmp is not None:
            sql += ' INNER JOIN `%s`.`%s` AS gt ON gt.`id` = br.`group_id`' % groups_tmp

        if sites_tmp is not None:
            sql += ' INNER JOIN `%s`.`%s` AS st ON st.`id` = dr.`site_id`' % sites_tmp

        if datasets_tmp is not None:
            sql += ' INNER JOIN `%s`.`%s` AS dt ON dt.`id` = dr.`dataset_id`' % datasets_tmp

        sql += ' ORDER BY dr.`dataset_id`, dr.`site_id`'

        # Blocks are left joined -> there will be (# sites) x (# blocks) entries per dataset

        _dataset_id = 0
        _site_id = 0
        dataset_replica = None
        for dataset_id, site_id, block_id, group_id, is_complete, b_is_custodial, b_size, b_last_update in self._mysql.xquery(sql):
            if dataset_id != _dataset_id:
                _dataset_id = dataset_id

                dataset = id_dataset_map[_dataset_id]
                dataset.replicas.clear()

                id_block_map = id_block_maps[dataset_id]

            if site_id != _site_id:
                _site_id = site_id
                site = id_site_map[site_id]

            if dataset_replica is None or dataset is not dataset_replica.dataset or site is not dataset_replica.site:
                if dataset_replica is not None:
                    # this is the last dataset_replica
                    # add to dataset and site after filling all block replicas
                    # this does not matter for the dataset, but for the site there is some heavy
                    # computation needed when a replica is added
                    dataset_replica.dataset.replicas.add(dataset_replica)
                    dataset_replica.site.add_dataset_replica(dataset_replica, add_block_replicas = True)

                dataset_replica = DatasetReplica(
                    dataset,
                    site
                )

            if block_id is None:
                # this block replica does not exist
                continue

            block = id_block_map[block_id]
            group = id_group_map[group_id]

            block_replica = BlockReplica(
                block,
                site,
                group = group,
                is_complete = (is_complete == 1),
                is_custodial = (b_is_custodial == 1),
                size = block.size if b_size is None else b_size,
                last_update = b_last_update
            )

            dataset_replica.block_replicas.add(block_replica)
            block.replicas.add(block_replica)

        if dataset_replica is not None:
            # one last bit
            dataset_replica.dataset.replicas.add(dataset_replica)
            dataset_replica.site.add_dataset_replica(dataset_replica, add_block_replicas = True)

    def _setup_constraints(self, table, names):
        columns = ['`id` int(11) unsigned NOT NULL', 'PRIMARY KEY (`id`)']
        tmp_db, tmp_table = self._mysql.create_tmp_table(table + '_load', columns)

        # first dump the group ids into a temporary table, then constrain the original table
        sqlbase = 'INSERT INTO `%s`.`%s` SELECT `id` FROM `%s`' % (tmp_db, tmp_table, table)
        self._mysql.execute_many(sqlbase, 'name', names)

        return tmp_db, tmp_table

    def save_data(self, inventory): #override
        ## Save groups
        LOG.info('Saving groups.')

        self._save_groups(inventory)

        LOG.info('Saved %d groups.', len(inventory.groups))

        ## Save sites
        LOG.info('Saving sites.')

        self._save_sites(inventory)

        LOG.info('Saved %d sites.', len(inventory.sites))

        ## Save datasets
        LOG.info('Saving datasets.')

        self._save_datasets(inventory)

        LOG.info('Saved %d datasets.', len(inventory.datasets))

        ## Save blocks
        LOG.info('Saving blocks.')

        self._save_blocks(inventory)

        num_blocks = sum(len(d.blocks) for d in inventory.datasets.itervalues())

        LOG.info('Saved %d blocks.', num_blocks)

        ## Save replicas (dataset and block in one go)
        LOG.info('Saving replicas.')

        self._save_replicas(inventory)

        num_dataset_replicas = 0
        num_block_replicas = 0
        for dataset in inventory.datasets.itervalues():
            num_dataset_replicas += len(dataset.replicas)
            num_block_replicas += sum(len(r.block_replicas) for r in dataset.replicas)

        LOG.info('Saved %d dataset replicas and %d block replicas.', num_dataset_replicas, num_block_replicas)

    def _save_groups(self, inventory):
        self._mysql.query('DROP TABLE IF EXISTS `groups_tmp`')
        self._mysql.query('CREATE TABLE `groups_tmp` LIKE `groups`')

        sql = 'INSERT INTO `groups_tmp` (`name`, `olevel`) VALUES (%s, %s)'
        for group in inventory.groups.itervalues():
            if group.name is None:
                # skip the null group
                continue

            group.id = self._mysql.query(sql, group.name, Group.olevel_name(group.olevel))

        self._mysql.query('DROP TABLE `groups`')
        self._mysql.query('RENAME TABLE `groups_tmp` TO `groups`')

    def _save_sites(self, inventory):
        self._mysql.query('DROP TABLE IF EXISTS `sites_tmp`')
        self._mysql.query('CREATE TABLE `sites_tmp` LIKE `sites`')

        sql = 'INSERT INTO `sites_tmp` (`name`, `host`, `storage_type`, `backend`, `status`) VALUES (%s, %s, %s, %s, %s)'
        for site in inventory.sites.itervalues():
            site.id = self._mysql.query(sql, site.name, site.host, Site.storage_type_name(site.storage_type), \
                site.backend, Site.status_name(site.status))

        self._mysql.query('DROP TABLE `sites`')
        self._mysql.query('RENAME TABLE `sites_tmp` TO `sites`')

    def _save_datasets(self, inventory):
        software_versions = set()
        for dataset in inventory.datasets.itervalues():
            software_versions.add(dataset.software_version)

        self._mysql.query('DROP TABLE IF EXISTS `software_versions_tmp`')
        self._mysql.query('CREATE TABLE `software_versions_tmp` LIKE `software_versions`')
        fields = ('cycle', 'major', 'minor', 'suffix')
        self._mysql.insert_many('software_versions_tmp', fields, None, software_versions, do_update = False)

        self._mysql.query('DROP TABLE `software_versions`')
        self._mysql.query('RENAME TABLE `software_versions_tmp` TO `software_versions`')

        version_id_map = {}
        sql = 'SELECT `id`, `cycle`, `major`, `minor`, `suffix` FROM `software_versions`'
        for vid, cycle, major, minor, suffix in self._mysql.xquery(sql):
            version_id_map[(cycle, major, minor, suffix)] = vid

        self._mysql.query('DROP TABLE IF EXISTS `datasets_tmp`')
        self._mysql.query('CREATE TABLE `datasets_tmp` LIKE `groups`')

        sql = 'INSERT INTO `datasets` (`name`, `size`, `num_files`, `status`, `data_type`, `software_version_id`, `last_update`, `is_open`)'
        sql += ' VALUES (%s, %s, %s, %s, %s, %s, %s, %s)'
        for dataset in inventory.datasets.itervalues():
            dataset.id = self._mysql.query(sql, dataset.name, dataset.size, dataset.num_files, \
                dataset.status, dataset.data_type, version_id_map[dataset.software_version],
                time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(dataset.last_update)), dataset.is_open)

        self._mysql.query('DROP TABLE `datasets`')
        self._mysql.query('RENAME TABLE `datasets_tmp` TO `datasets`')

    def _save_blocks(self, inventory):
        self._mysql.query('DROP TABLE IF EXISTS `blocks_tmp`')
        self._mysql.query('CREATE TABLE `blocks_tmp` LIKE `blocks`')

        sql = 'INSERT INTO `blocks` (`dataset_id`, `name`, `size`, `num_files`, `is_open`, `last_update`)'
        sql += ' VALUES (%s, %s, %s, %s, %s, %s)'

        for dataset in inventory.datasets.itervalues():
            for block in dataset.blocks:
                self._mysql.query(sql, dataset.id, block.real_name(), \
                    block.size, block.num_files, block.is_open, \
                    time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(block.last_update)))

        self._mysql.query('DROP TABLE `blocks`')
        self._mysql.query('RENAME TABLE `blocks_tmp` TO `blocks`')

    def _save_replicas(self, inventory):
        self._mysql.query('DROP TABLE IF EXISTS `dataset_replicas_tmp`')
        self._mysql.query('CREATE TABLE `dataset_replicas_tmp` LIKE `dataset_replicas`')

        fields = ('dataset_id', 'site_id')
        for site in inventory.sites.itervalues():
            mapping = lambda replica: (replica.dataset.id, site.id)

            self._mysql.insert_many('dataset_replicas_tmp', fields, mapping, site.dataset_replicas(), do_update = False)

        self._mysql.query('DROP TABLE `dataset_replicas`')
        self._mysql.query('RENAME TABLE `dataset_replicas_tmp` TO `dataset_replicas`')

        self._mysql.query('DROP TABLE IF EXISTS `block_replicas_tmp`')
        self._mysql.query('CREATE TABLE `block_replicas_tmp` LIKE `block_replicas`')
        self._mysql.query('DROP TABLE IF EXISTS `block_replica_sizes_tmp`')
        self._mysql.query('CREATE TABLE `block_replica_sizes_tmp` LIKE `block_replica_sizes`')

        replica_fields = ('block_id', 'site_id', 'group_id', 'is_complete', 'is_custodial', 'last_update')
        size_fields = ('block_id', 'site_id', 'size')

        for site in inventory.sites.itervalues():
            for dataset_replica in site.dataset_replicas():
                mapping = lambda replica: (replica.block.id, site.id, \
                    replica.group.id, replica.is_complete, replica.is_custodial, \
                    time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(replica.last_update)))

                self._mysql.insert_many('block_replicas_tmp', replica_fields, mapping, dataset_replica.block_replicas, do_update = False)
                
                mapping = lambda replica: (replica.block.id, site.id, replica.size)

                incomplete_replicas = [r for r in dataset_replica.block_replicas if r.size != r.block.size]
                self._mysql.inert_many('block_replica_sizes_tmp', size_fields, mapping, incomplete_replicas, do_update = False)

        self._mysql.query('DROP TABLE `block_replicas`')
        self._mysql.query('RENAME TABLE `block_replicas_tmp` TO `block_replicas`')

        self._mysql.query('DROP TABLE `block_replica_sizes`')
        self._mysql.query('RENAME TABLE `block_replica_sizes_tmp` TO `block_replica_sizes`')

    def save_block(self, block): #override
        dataset_id = block.dataset.id
        if dataset_id == 0:
            return

        fields = ('dataset_id', 'name', 'size', 'num_files', 'is_open', 'last_update')
        block_id = self._insert_update('blocks', fields, dataset_id, block.real_name(), block.size, block.num_files, block.is_open, time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(block.last_update)))

        if block_id != 0:
            # new insert
            block.id = block_id

    def delete_block(self, block): #override
        dataset_id = block.dataset.id
        if dataset_id == 0:
            return

        sql = 'DELETE FROM b, f, r USING `blocks` AS b'
        sql += ' LEFT JOIN `block_replicas` AS r ON r.`block_id` = b.`id`'
        sql += ' LEFT JOIN `files` AS f ON f.`block_id` = b.`id`'
        sql += ' WHERE b.`dataset_id` = %s AND b.`name` = %s'

        self._mysql.query(sql, dataset_id, block.real_name())

    def save_file(self, lfile): #override
        dataset_id = lfile.block.dataset.id
        if dataset_id == 0:
            return

        block_id = lfile.block.id
        if block_id == 0:
            return

        fields = ('block_id', 'dataset_id', 'size', 'name')
        file_id = self._insert_update('files', fields, block_id, dataset_id, lfile.size, lfile.lfn)

        if file_id != 0:
            # new insert
            lfile.id = file_id

    def delete_file(self, lfile): #override
        sql = 'DELETE FROM `files` WHERE `name` = %s'
        self._mysql.query(sql, lfile.lfn)

    def save_blockreplica(self, block_replica): #override
        block_id = block_replica.block.id
        if block_id == 0:
            return

        site_id = block_replica.site.id
        if site_id == 0:
            return

        fields = ('block_id', 'site_id', 'group_id', 'is_complete', 'is_custodial', 'last_update')
        self._insert_update('block_replicas', fields, block_id, site_id, \
            block_replica.group.id, block_replica.is_complete, block_replica.is_custodial, \
            time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(block_replica.last_update)))

        if block_replica.size != block_replica.block.size:
            fields = ('block_id', 'site_id', 'size')
            self._insert_update('block_replica_sizes', fields, block_id, site_id, block_replica.size)
        else:
            sql = 'DELETE FROM `block_replica_sizes` WHERE `block_id` = %s AND `site_id` = %s'
            self._mysql.query(sql, block_id, site_id)

    def delete_blockreplica(self, block_replica): #override
        dataset_id = block_replica.block.dataset.id
        if dataset_id == 0:
            return

        block_id = block_replica.block.id
        if block_id == 0:
            return

        site_id = block_replica.site.id
        if site_id == 0:
            return

        sql = 'DELETE FROM `block_replicas` WHERE `block_id` = %s AND `site_id` = %s'
        self._mysql.query(sql, block_id, site_id)

        sql = 'DELETE FROM `block_replica_sizes` WHERE `block_id` = %s AND `site_id` = %s'
        self._mysql.query(sql, block_id, site_id)

        sql = 'SELECT COUNT(*) FROM `block_replicas` AS br'
        sql += ' INNER JOIN `blocks` AS b ON b.`id` = br.`block_id`'
        sql += ' WHERE b.`dataset_id` = %s AND br.`site_id` = %s'
        if self._mysql.query(sql, dataset_id, site_id)[0] == 0:
            sql = 'DELETE FROM `dataset_replicas` WHERE `dataset_id` = %s AND `site_id` = %s'
            self._mysql.query(sql, dataset_id, site_id)

    def save_dataset(self, dataset): #override
        if dataset.software_version is None:
            software_version_id = 0
        else:
            sql = 'SELECT `id` FROM `software_versions` WHERE (`cycle`, `major`, `minor`, `suffix`) = (%s, %s, %s, %s)'
            
            result = self._mysql.query(sql, *dataset.software_version)
            if len(result) == 0:
                sql = 'INSERT INTO `software_versions` (`cycle`, `major`, `minor`, `suffix`) VALUES (%s, %s, %s, %s)'
                software_version_id = self._mysql.query(sql, *dataset.software_version)
            else:
                software_version_id = result[0]
            
        fields = ('name', 'size', 'num_files', 'status', 'data_type', 'software_version_id', 'last_update', 'is_open')
        dataset_id = self._insert_update('datasets', fields, dataset.name, dataset.size, dataset.num_files, \
            dataset.status, dataset.data_type, software_version_id,
            time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(dataset.last_update)), dataset.is_open)

        if dataset_id != 0:
            # new insert
            dataset.id = dataset_id

    def delete_dataset(self, dataset): #override
        sql = 'DELETE FROM d, b, f, dr, br, brs USING `datasets` AS d'
        sql += ' LEFT JOIN `blocks` AS b ON b.`dataset_id` = d.`id`'
        sql += ' LEFT JOIN `files` AS f ON f.`dataset_id` = d.`id`'
        sql += ' LEFT JOIN `dataset_replicas` AS dr ON dr.`dataset_id` = d.`id`'
        sql += ' LEFT JOIN `block_replicas` AS br ON br.`block_id` = b.`id`'
        sql += ' LEFT JOIN `block_replica_sizes` AS brs ON brs.`block_id` = b.`id`'
        sql += ' WHERE d.`name` = %s'

        self._mysql.query(sql, dataset.name)

    def save_datasetreplica(self, dataset_replica): #override
        dataset_id = dataset_replica.dataset.id
        if dataset_id == 0:
            return

        site_id = dataset_replica.site.id
        if site_id == 0:
            return

        fields = ('dataset_id', 'site_id')
        self._insert_update('dataset_replicas', fields, dataset_id, site_id)

    def delete_datasetreplica(self, dataset_replica): #override
        dataset_id = dataset_replica.dataset.id
        if dataset_id == 0:
            return

        site_id = dataset_replica.site.id
        if site_id == 0:
            return

        sql = 'DELETE FROM br, brs USING `blocks` AS b'
        sql += ' INNER JOIN `block_replicas` AS br ON br.`block_id` = b.`id`'
        sql += ' LEFT JOIN `block_replica_sizes` AS brs ON brs.`block_id` = b.`id` AND brs.`site_id` = br.`site_id`'
        sql += ' WHERE b.`dataset_id` = %s AND br.`site_id` = %s'

        self._mysql.query(sql, dataset_id, site_id)

        sql = 'DELETE FROM `dataset_replicas` WHERE `dataset_id` = %s AND `site_id` = %s'
        self._mysql.query(sql, dataset_id, site_id)

    def save_group(self, group): #override
        fields = ('name', 'olevel')
        group_id = self._insert_update('groups', fields, group.name, Group.olevel_name(group.olevel))

        if group_id != 0:
            group.id = group_id

    def delete_group(self, group): #override
        sql = 'DELETE FROM `groups` WHERE `id` = %s'
        self._mysql.query(sql, group.id)

        sql = 'UPDATE `block_replicas` SET `group_id` = 0 WHERE `group_id` = %s'
        self._mysql.query(sql, group.id)

    def save_partition(self, partition): #override
        fields = ('name',)
        self._insert_update('partitions', fields, partition.name)

        # For new partitions, persistency requires saving site partition data with default parameters.
        # We handle missing site partition entries at load time - if a row is missing, SitePartition object with
        # default parameters will be created.

    def delete_partition(self, partition): #override
        sql = 'DELETE FROM p, q USING `partitions` AS p'
        sql += ' LEFT JOIN `quotas` AS q ON q.`partition_id` = p.`id`'
        sql += ' WHERE p.`name` = %s'
        self._mysql.query(sql, partition.name)

    def save_site(self, site): #override
        fields = ('name', 'host', 'storage_type', 'backend', 'status')
        site_id = self._insert_update('sites', fields, site.name, site.host, site.storage_type, site.backend, site.status)

        if site_id != 0:
            site.id = site_id

        # For new sites, persistency requires saving site partition data with default parameters.
        # We handle missing site partition entries at load time - if a row is missing, SitePartition object with
        # default parameters will be created.

    def delete_site(self, site): #override
        sql = 'DELETE FROM s, dr, br, brs, q USING `sites` AS s'
        sql += ' LEFT JOIN `dataset_replicas` AS dr ON dr.`site_id` = s.`id`'
        sql += ' LEFT JOIN `block_replicas` AS br ON br.`site_id` = s.`id`'
        sql += ' LEFT JOIN `block_replica_sizes` AS brs ON brs.`site_id` = s.`id`'
        sql += ' LEFT JOIN `quotas` AS q ON q.`site_id` = s.`id`'
        sql += ' WHERE s.`name` = %s'
        self._mysql.query(sql, site.name)

    def save_sitepartition(self, site_partition): #override
        # We are only saving quotas. For superpartitions, there is nothing to do.
        if site_partition.partition.subpartitions is not None:
            return

        site_id = site_partition.site.id
        if site_id == 0:
            return

        sql = 'SELECT `id` FROM `partitions` WHERE `name` = %s'
        result = self._mysql.query(sql, site_partition.partition.name)
        if len(result) == 0:
            return

        partition_id = result[0]

        fields = ('site_id', 'partition_id', 'storage')
        self._insert_update('quotas', fields, site_id, partition_id, site_partition.quota * 1.e-12)

    def _insert_update(self, table, fields, *values):
        placeholders = ', '.join(['%s'] * len(fields))

        sql = 'INSERT INTO `%s` (' % table
        sql += ', '.join('`%s`' % f for f in fields)
        sql += ') VALUES (' + placeholders + ')'
        sql += ' ON DUPLICATE KEY UPDATE '
        sql += ', '.join(['`%s`=VALUES(`%s`)' % (f, f) for f in fields])

        return self._mysql.query(sql, *values)
