CREATE TABLE `fts_file_transfers` (
  `id` bigint(20) unsigned NOT NULL,
  `fts_batch_id` bigint(20) unsigned NOT NULL,
  `fts_file_id` bigint(20) unsigned NOT NULL,
  PRIMARY KEY (`id`),
  KEY `fts` (`fts_batch_id`,`fts_file_id`)
) ENGINE=MyISAM DEFAULT CHARSET=latin1 COLLATE=latin1_general_cs;
