-module(mod_transport_telegram).

-behaviour(gen_mod).

-export([start/2, stop/1, depends/2, mod_options/1, mod_doc/0]).

-define(NS_TRANSPORT_TELEGRAM, <<"urn:xabber:transport:telegram:1">>).

start(_Host, _Opts) ->
    ok.

stop(_Host) ->
    ok.

depends(_Host, _Opts) ->
    [].

mod_options(_Host) ->
    [{allowed_components, []}].

mod_doc() ->
    #{
        desc => <<"Roster helper for xmpp-transport-telegram">>,
        opts => [
            {allowed_components, #{
                value => <<"list of component domains allowed to manage Telegram roster contacts">>
            }}
        ]
    }.
