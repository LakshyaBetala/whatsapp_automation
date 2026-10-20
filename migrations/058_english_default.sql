-- 058_english_default.sql
-- Customer messages are English unless the owner picks Hinglish. The old column
-- default ('hinglish', migration 013) was never a choice anyone made, so shops
-- still on it move to English. Batches keep whatever language the owner set.
alter table businesses alter column msg_language set default 'english';
update businesses set msg_language = 'english'
 where msg_language is null or msg_language = 'hinglish';
