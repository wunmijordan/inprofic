/* Restored, illustrative INPROFIC walkthroughs. No live business data. */

(() => {
    const stage = document.querySelector('[data-promo-stage]'); if (!stage) return;
    const slides = [...stage.querySelectorAll('[data-promo-slide]')]; const bars = [...stage.querySelectorAll('.promo-progress span')];
    const motion=matchMedia('(prefers-reduced-motion: reduce)');
    const syncMedia=()=>stage.querySelectorAll('video').forEach(video=>{if(motion.matches||document.hidden||!video.closest('[data-promo-slide]')?.classList.contains('is-active'))video.pause();else video.play().catch(()=>{});});
    syncMedia();motion.addEventListener('change',syncMedia);document.addEventListener('visibilitychange',syncMedia);
    if (slides.length < 2) return;
    let index = 0, timer;
    const show = next => { slides[index].classList.remove('is-active'); slides[index].setAttribute('aria-hidden','true'); bars[index]?.classList.remove('is-active'); index = next % slides.length; slides[index].classList.add('is-active'); slides[index].setAttribute('aria-hidden','false'); bars[index]?.classList.add('is-active'); syncMedia(); };
    const start = () => { clearInterval(timer); if(motion.matches||document.hidden)return; timer = setInterval(() => show(index + 1), 7000); };
    motion.addEventListener('change',()=>{if(motion.matches)clearInterval(timer);else start();});
    document.addEventListener('visibilitychange',()=>{if(document.hidden)clearInterval(timer);else start();});
    stage.addEventListener('mouseenter', () => clearInterval(timer)); stage.addEventListener('mouseleave', start); stage.addEventListener('focusin', () => clearInterval(timer)); stage.addEventListener('focusout', start); start();
  })();

(() => {
    const serviceProfiles = {
      bakery:{label:'Bakery',usesProduction:true,stockLabel:'Flour',stockMeta:'68 kg',secondary:'Butter',secondaryMeta:'14 kg',product:'Mini loaves',productMeta:'48 ready',supplier:'BakeSupply',production:'Bake batch #021',productionTarget:'120 loaves',channel:'Online order',counter:'Bakery counter',customer:'Celebration order',delivery:'Pickup / delivery'},
      restaurant:{label:'Restaurant & Food Service',usesProduction:true,stockLabel:'Long grain rice',stockMeta:'68 kg',secondary:'Cooking oil',secondaryMeta:'6 L',product:'Jollof + Chicken',productMeta:'42 portions',supplier:'KitchenMart',production:'Jollof prep #021',productionTarget:'120 portions',channel:'Delivery / online',counter:'Restaurant POS',customer:'Table / delivery order',delivery:'Rider / provider'},
      general:{label:'General Production',usesProduction:true,stockLabel:'Steel sheet',stockMeta:'84 sheets',secondary:'Fasteners',secondaryMeta:'1,240 pcs',product:'Filter Unit',productMeta:'36 units',supplier:'Industrial Supply Co.',production:'Assembly run #021',productionTarget:'50 units',channel:'Online / customer order',counter:'Direct sales desk',customer:'Customer order',delivery:'Dispatch / collection'},
      wholesale:{label:'Wholesale',usesProduction:false,stockLabel:'Detergent cartons',stockMeta:'84 cartons',secondary:'Bottled water',secondaryMeta:'128 cases',product:'Trade stock',productMeta:'212 units',supplier:'Prime Distribution',production:'',productionTarget:'',channel:'Wholesale / online trade',counter:'Trade counter',customer:'Trade order',delivery:'Warehouse dispatch'},
      retail:{label:'Retail',usesProduction:false,stockLabel:'Sneakers',stockMeta:'38 pairs',secondary:'T-shirts',secondaryMeta:'74 pcs',product:'Shop stock',productMeta:'186 units',supplier:'City Merchants',production:'',productionTarget:'',channel:'Retail / online',counter:'Retail POS',customer:'Customer basket',delivery:'Pickup / delivery'}
    };
    let activeService = localStorage.getItem('inprofic-marketing-demo-service') || 'general';
    if (!serviceProfiles[activeService]) activeService = 'general';

    const good = /healthy|ready|paid|complete|delivered|posted|active|live|verified|received|confirmed|done|published|matched|available|released|reconciled/i;
    const warn = /low|verify|issue|due|awaiting|next|on route|running|active|pending|attention/i;
    const statusClass = value => good.test(String(value||'')) ? 'good' : warn.test(String(value||'')) ? 'warn' : '';

    function generalConfig(profile) {
      const productionFields = profile.usesProduction
        ? [
            {id:'fg-name',label:'Finished good',value:profile.product},
            {id:'fg-unit',label:'Base production unit',value:profile.label === 'Restaurant & food service' ? 'portion' : profile.label === 'Bakery' ? 'loaf' : 'unit'},
            {id:'fg-sale',label:'Customer selling unit',value:profile.label === 'Restaurant & food service' ? 'plate / pack' : 'unit / pack'},
            {id:'fg-price',label:'Selling price',value:'₦ configured'},
          ]
        : [
            {id:'fg-name',label:'Stock product',value:profile.product},
            {id:'fg-unit',label:'Stock unit',value:profile.label === 'Wholesale' ? 'carton / unit' : 'piece / pair'},
            {id:'fg-sale',label:'Customer selling unit',value:profile.label === 'Wholesale' ? 'case / unit' : 'piece'},
            {id:'fg-price',label:'Selling price',value:'₦ configured'},
          ];

      const procurement = {
        tab:'Buy', module:'Procurement', scenes:[
          {eyebrow:'Purchase orders',title:'Open supplier purchases',state:'Pending',caption:`${profile.label} purchasing starts with a supplier-side record before stock changes.`,kpis:[['Supplier',profile.supplier],['POs','2 open'],['Status','Awaiting']],rows:[[profile.stockLabel,'Expected '+profile.stockMeta,'Pending'],[profile.secondary,'Expected '+profile.secondaryMeta,'Pending'],['Supplier terms','Recorded','Active']],actions:[{id:'receive-po',label:'Receive purchase',primary:true,nextScene:1}],auto:['receive-po']},
          {eyebrow:'Goods received',title:'Receive against the purchase',state:'Received',caption:'Receiving posts the arrival, updates stock and preserves the supplier cost trail.',kpis:[['Supplier',profile.supplier],['Arrival',profile.stockMeta],['Stock','Updated']],rows:[[profile.stockLabel,profile.stockMeta,'Received'],[profile.secondary,profile.secondaryMeta,'Received'],['Purchase cost','Captured','Posted']],actions:[{id:'open-inventory',label:'Open Inventory',primary:true,nextModule:'Inventory'}],auto:['module-Inventory']}
        ]
      };

      const inventoryScenes = profile.usesProduction ? [
        {eyebrow:'Inventory overview',title:'Stock is separated by what it represents',state:'Live',caption:'Move from the inventory overview into raw materials and finished goods without leaving the module.',kpis:[['Raw materials','2 tracked'],['Finished goods',profile.productMeta],['Alerts','2']],rows:[[profile.stockLabel,profile.stockMeta,'Healthy'],[profile.secondary,profile.secondaryMeta,'Low'],[profile.product,profile.productMeta,'Ready']],subnav:[{id:'raw-materials',label:'Raw materials',nextScene:1},{id:'finished-goods',label:'Finished goods',nextScene:4}],auto:['raw-materials']},
        {eyebrow:'Raw materials',title:'Raw material stock',state:'Live',caption:'Production inputs keep their own purchase units, stock units, warnings and movement history.',kpis:[['Materials','2'],['Low stock','1'],['Movements','18']],rows:[[profile.stockLabel,profile.stockMeta,'Healthy'],[profile.secondary,profile.secondaryMeta,'Low'],['Packaging','32 packs','Healthy']],subnav:[{id:'raw-materials',label:'Raw materials',active:true},{id:'finished-goods',label:'Finished goods',nextScene:4}],actions:[{id:'add-raw',label:'+ Add raw material',primary:true,nextScene:2}],auto:['add-raw']},
        {eyebrow:'Raw material form',title:'Add a raw material',state:'Editing',caption:'The form captures the real buying and stock relationship before production ever uses the material.',kpis:[['Unit model','Configured'],['Reorder','Enabled'],['Supplier','Optional']],fields:[{id:'raw-name',label:'Material name',value:profile.stockLabel},{id:'raw-purchase-unit',label:'Purchase unit',value:profile.label==='Restaurant & food service'?'bag / litre':'supplier unit'},{id:'raw-stock-unit',label:'Stock unit',value:profile.label==='Restaurant & food service'?'kg / ml':'measured unit'},{id:'raw-reorder',label:'Reorder level',value:'10'}],actions:[{id:'save-raw',label:'Save raw material',primary:true,nextScene:3}],auto:['raw-name','raw-stock-unit','save-raw']},
        {eyebrow:'Raw materials',title:'Material saved to inventory',state:'Saved',caption:'The new material is immediately available to purchasing, recipes and stock movement history.',kpis:[['Saved',profile.stockLabel],['Stock','Ready'],['Audit','Logged']],rows:[[profile.stockLabel,'New record','Done'],['Movement history','Available','Live'],['Recipe use','Available','Ready']],subnav:[{id:'raw-materials',label:'Raw materials',active:true},{id:'finished-goods',label:'Finished goods',nextScene:4}],auto:['finished-goods']},
        {eyebrow:'Finished goods',title:'Finished goods catalogue',state:'Live',caption:'Finished goods carry the sellable product definition while production keeps the input relationship behind it.',kpis:[['Products',profile.productMeta],['Channels','Connected'],['Warnings','0']],rows:[[profile.product,profile.productMeta,'Ready'],['Standard selling unit','Configured','Done'],['Channel pricing','Configured','Live']],subnav:[{id:'raw-materials',label:'Raw materials',nextScene:1},{id:'finished-goods',label:'Finished goods',active:true}],actions:[{id:'add-finished',label:'+ Add finished good',primary:true,nextScene:5}],auto:['add-finished']},
        {eyebrow:'Finished good form',title:'Add a finished good',state:'Editing',caption:'The product form holds the unit, selling and channel definition; production configuration stays specific to production-capable services.',kpis:[['Product','New'],['Selling','Configuring'],['Channels','Available']],fields:productionFields,actions:[{id:'save-finished',label:'Save finished good',primary:true,nextScene:6}],auto:['fg-name','fg-sale','save-finished']},
        {eyebrow:'Finished goods',title:'Sellable product saved',state:'Saved',caption:'The product can now be referenced by recipes/production and by its permitted selling channels.',kpis:[['Product',profile.product],['Stock','Ready'],['Audit','Logged']],rows:[[profile.product,'Product record','Done'],['Customer unit','Configured','Done'],['Channel availability','Configured','Live']],actions:[{id:'open-production',label:'Open Production',primary:true,nextModule:'Production'}],auto:['module-Production']}
      ] : [
        {eyebrow:'Stock inventory',title:'Purchased-for-resale stock',state:'Live',caption:`${profile.label} stays stock-first: procure products, hold stock and sell them without production-only concepts.`,kpis:[['Products',profile.productMeta],['Low stock','1'],['Movements','24']],rows:[[profile.stockLabel,profile.stockMeta,'Healthy'],[profile.secondary,profile.secondaryMeta,'Low'],[profile.product,profile.productMeta,'Ready']],subnav:[{id:'stock-products',label:'Stock products',nextScene:1}],auto:['stock-products']},
        {eyebrow:'Stock products',title:'Product catalogue',state:'Live',caption:'Stock products combine procurement units, stock quantities, pack options and selling channels.',kpis:[['Products','12'],['Suppliers','4'],['Channels','2']],rows:[[profile.product,profile.productMeta,'Ready'],[profile.stockLabel,profile.stockMeta,'Healthy'],['Bulk / pack options','Available','Live']],subnav:[{id:'stock-products',label:'Stock products',active:true}],actions:[{id:'add-stock-product',label:'+ Add stock product',primary:true,nextScene:2}],auto:['add-stock-product']},
        {eyebrow:'Stock product form',title:'Add a purchased product',state:'Editing',caption:'There are no recipes, batches or raw-material relationships—only the stock and selling information the business needs.',kpis:[['Source','Procured'],['Stock','Configuring'],['Channels','Available']],fields:productionFields,actions:[{id:'save-stock-product',label:'Save stock product',primary:true,nextScene:3}],auto:['fg-name','fg-unit','save-stock-product']},
        {eyebrow:'Stock products',title:'Product saved to inventory',state:'Saved',caption:'The product is ready for procurement, POS, online selling or distribution according to its channel settings.',kpis:[['Product',profile.product],['Stock','Ready'],['Audit','Logged']],rows:[[profile.product,'Product record','Done'],['Procurement unit','Configured','Done'],['Selling channels','Configured','Live']],actions:[{id:'open-commerce',label:'Open Commerce',primary:true,nextModule:'Commerce'}],auto:['module-Commerce']}
      ];

      const steps = [procurement,{tab:'Stock',module:'Inventory',scenes:inventoryScenes}];
      if (profile.usesProduction) steps.push({tab:'Make',module:'Production',scenes:[
        {eyebrow:'Production queue',title:'Production orders',state:'Ready',caption:'Production starts from an intentional order using the finished-good configuration you just created.',kpis:[['Product',profile.product],['Open runs','2'],['Inputs','Available']],rows:[[profile.production,'Planned','Ready'],['Materials','Checked','Done'],['Target',profile.productionTarget,'Ready']],actions:[{id:'create-production',label:'+ Create production',primary:true,nextScene:1}],auto:['create-production']},
        {eyebrow:'Production form',title:'Plan the production run',state:'Editing',caption:'Select the product and target; INPROFIC resolves the configured recipe/formula inputs before release.',kpis:[['Recipe / formula','Linked'],['Stock','Checked'],['Target',profile.productionTarget]],fields:[{id:'prod-product',label:'Finished good',value:profile.product},{id:'prod-target',label:'Target output',value:profile.productionTarget},{id:'prod-date',label:'Production date',value:'Today'},{id:'prod-method',label:'Sizing method',value:'Product quantity'}],actions:[{id:'release-inputs',label:'Release inputs',primary:true,nextScene:2}],auto:['prod-product','prod-target','release-inputs']},
        {eyebrow:'Production in progress',title:profile.production,state:'Running',caption:'Released inputs and measurable output stay attached to this production run.',kpis:[['Target',profile.productionTarget],['Inputs','Released'],['Yield','96%']],progress:76,rows:[['Inputs / materials','Released','Done'],['Work in progress','76%','Running'],['Output / quality','Measured','Logged']],actions:[{id:'complete-batch',label:'Complete production',primary:true,nextScene:3}],auto:['complete-batch']},
        {eyebrow:'Production complete',title:'Finished output posted',state:'Complete',caption:'Output returns to finished-goods stock with its production and material history intact.',kpis:[['Output',profile.productionTarget],['Stock','Updated'],['Audit','Logged']],rows:[[profile.product,'Output posted','Done'],['Material usage','Finalised','Done'],['Cost snapshot','Captured','Posted']],actions:[{id:'open-commerce',label:'Open Commerce',primary:true,nextModule:'Commerce'}],auto:['module-Commerce']}
      ]});
      steps.push(
        {tab:'Sell',module:'Commerce',scenes:[
          {eyebrow:profile.channel,title:'Orders and channels',state:'Live',caption:'Selling starts from the same product record, with the correct price and availability for each channel.',kpis:[['Channel',profile.channel],['Product',profile.product],['Orders','3']],rows:[[profile.product,'Available','Live'],['Online / direct price','Applied','Matched'],['Latest order','Awaiting payment','Pending']],actions:[{id:'open-order',label:'Open latest order',primary:true,nextScene:1}],auto:['open-order']},
          {eyebrow:'Order detail',title:profile.customer,state:'Verified',caption:'Payment, product choice and fulfilment state remain one connected order record.',kpis:[['Product',profile.product],['Payment','Confirmed'],['Fulfilment','Ready']],rows:[[profile.product,'× 2','Ready'],['Channel price','Applied','Matched'],['Payment','Verified','Posted']],actions:[{id:'open-delivery',label:'Send to Delivery',primary:true,nextModule:'Delivery'}],auto:['module-Delivery']}
        ]},
        {tab:'Deliver',module:'Delivery',scenes:[
          {eyebrow:'Delivery queue',title:'Ready for fulfilment',state:'Ready',caption:'The paid order enters the delivery workspace without losing customer or payment context.',kpis:[['Method',profile.delivery],['Jobs','2'],['Status','Ready']],rows:[[profile.customer,'Paid','Done'],['Delivery quote','Accepted','Done'],['Assignment','Not assigned','Next']],actions:[{id:'assign-delivery',label:'Assign delivery',primary:true,nextScene:1}],auto:['assign-delivery']},
          {eyebrow:'Active delivery',title:profile.delivery,state:'On route',caption:'Assignment, tracking and proof remain synchronized to the same order timeline.',kpis:[['Method',profile.delivery],['Status','On route'],['Proof','Pending']],progress:64,rows:[['Order','Ready','Done'],['Handoff','Assigned','Done'],['Destination','Customer','On route']],actions:[{id:'mark-delivered',label:'Mark delivered',primary:true,nextScene:2}],auto:['mark-delivered']},
          {eyebrow:'Delivery complete',title:'Delivered with proof',state:'Delivered',caption:'The completed delivery is visible to operations, the customer tracking view and reporting.',kpis:[['Status','Delivered'],['Proof','Saved'],['Updates','Synced']],rows:[['Customer tracking','Delivered','Done'],['Proof of delivery','Saved','Done'],['Order','Fulfilled','Complete']],actions:[{id:'open-finance',label:'Review Finance',primary:true,nextModule:'Finance'}],auto:['module-Finance']}
        ]},
        {tab:'Review',module:'Finance',scenes:[
          {eyebrow:'Financial review',title:'Operational value reconciled',state:'Posted',caption:'The money view keeps its source order, stock and fulfilment context.',kpis:[['Revenue','Matched'],['Cost','Captured'],['Cash','Reconciled']],rows:[['Customer payment','Matched','Posted'],['Sale revenue','Linked','Posted'],['Operating cost','Traceable','Reconciled']],actions:[{id:'restart-flow',label:'Replay workflow',nextModule:'Procurement'}],auto:['module-Procurement']}
        ]}
      );
      return {nav:steps.map(step=>step.module),steps};
    }

    function storefrontConfig(profile) {
      return {nav:['Catalogue','Checkout','POS','Payments','Operations','Delivery'],steps:[
        {tab:'Publish',module:'Catalogue',scenes:[
          {eyebrow:'Product catalogue',title:'Choose what customers can see',state:'Live',channel:'External',caption:`${profile.label} storefront publishing starts from the business product catalogue.`,kpis:[['Product',profile.product],['Categories','Active'],['Online','Enabled']],rows:[[profile.product,'Public product','Live'],['Category','Customer-facing','Active'],['Physical Store price','Internal only','Protected']],actions:[{id:'edit-public-product',label:'Edit public product',primary:true,nextScene:1}],auto:['edit-public-product']},
          {eyebrow:'Public product form',title:'Configure storefront selling',state:'Editing',channel:'External',caption:'Only external-safe fields and channel prices are represented here; Physical Store pricing remains internal.',kpis:[['Channel','Online / Bulk'],['Stock','Available'],['Category','Selected']],fields:[{id:'catalog-name',label:'Product',value:profile.product},{id:'catalog-price',label:'Online price',value:'₦ configured'},{id:'catalog-category',label:'Category',value:'Featured products'},{id:'catalog-option',label:'Selling option',value:'Standard / plain option'}],actions:[{id:'publish-product',label:'Publish product',primary:true,nextScene:2}],auto:['catalog-name','catalog-price','publish-product']},
          {eyebrow:'External storefront',title:'Product is live for customers',state:'Published',channel:'External',caption:'The public catalogue now exposes only the permitted product information and external selling modes.',kpis:[['Product',profile.product],['Online','Live'],['Stock','Available']],rows:[[profile.product,'Public price','Published'],['Category','Featured products','Live'],['Physical Store price','Not exposed','Protected']],actions:[{id:'open-checkout',label:'Open storefront',primary:true,nextModule:'Checkout'}],auto:['module-Checkout']}
        ]},
        {tab:'Web order',module:'Checkout',scenes:[
          {eyebrow:'Customer storefront',title:'Browse and choose a product',state:'Open',channel:'External',caption:'The storefront uses the same catalogue truth while remaining customer-safe.',kpis:[['Products','12'],['Delivery','Available'],['Basket','0']],rows:[[profile.product,'Available','Live'],['Plain / pack option','Available','Live'],['Category','Featured','Active']],actions:[{id:'add-to-basket',label:'Add to basket',primary:true,nextScene:1}],auto:['add-to-basket']},
          {eyebrow:'Basket',title:'Review the customer basket',state:'Ready',channel:'External',caption:'Quantity, selling option and public pricing are fixed before checkout begins.',kpis:[['Basket','2 items'],['Subtotal','Calculated'],['Mode','Online']],rows:[[profile.product,'× 2','Added'],['Selling option','Selected','Done'],['Delivery','Quote next','Next']],actions:[{id:'continue-checkout',label:'Continue to checkout',primary:true,nextScene:2}],auto:['continue-checkout']},
          {eyebrow:'Checkout form',title:'Address and delivery details',state:'Editing',channel:'External',caption:'Customer address, order mode and delivery quote are captured before payment.',kpis:[['Basket','2 items'],['Delivery','Quoted'],['Checkout','Ready']],fields:[{id:'checkout-name',label:'Customer name',value:'A. Customer'},{id:'checkout-address',label:'Delivery address',value:'Saved / mapped address'},{id:'checkout-mode',label:'Order mode',value:'Online'},{id:'checkout-delivery',label:'Delivery quote',value:'₦ calculated'}],actions:[{id:'place-order',label:'Place web order',primary:true,nextScene:3}],auto:['checkout-address','checkout-delivery','place-order']},
          {eyebrow:'Web order created',title:'External checkout becomes an order',state:'Created',channel:'External',caption:'The external order now exists inside INPROFIC with the customer-safe channel data preserved.',kpis:[['Order','#1048'],['Payment','Pending'],['Delivery','Quoted']],rows:[[profile.product,'× 2','Ready'],['Checkout data','Saved','Done'],['Physical Store price','Never exposed','Protected']],actions:[{id:'compare-pos',label:'Compare in-premise POS',primary:true,nextModule:'POS'}],auto:['module-POS']}
        ]},
        {tab:'Counter',module:'POS',scenes:[
          {eyebrow:'In-premise POS',title:profile.counter,state:'Open',channel:'Internal',caption:'Counter staff see the internal direct-selling surface, including Physical Store pricing that never appears externally.',kpis:[['Channel','Physical Store'],['Basket','0'],['Staff','Authorised']],rows:[[profile.product,'Available','Live'],['Physical Store price','Internal','Protected'],['Distribution / bulk','Available','Live']],actions:[{id:'pos-add',label:'Add product',primary:true,nextScene:1}],auto:['pos-add']},
          {eyebrow:'POS basket',title:'Review the counter sale',state:'Ready',channel:'Internal',caption:'The staff basket uses the same stock truth with staff-controlled settlement methods.',kpis:[['Basket','2 items'],['Channel','In-premise'],['Receipt','Pending']],rows:[[profile.product,'× 1','Added'],['Customer','Optional','Ready'],['Payment method','Staff selects','Next']],actions:[{id:'pos-pay',label:'Take payment',primary:true,nextModule:'Payments'}],auto:['module-Payments']}
        ]},
        {tab:'Pay',module:'Payments',scenes:[
          {eyebrow:'Payment confirmation',title:'Confirm authoritative paid state',state:'Review',channel:'Internal + External',caption:'Gateway, direct transfer and counter settlement converge on one payment truth.',kpis:[['Gateway','Auto'],['Transfer','Review'],['POS','Staff']],rows:[['Online gateway','Trusted callback','Verified'],['Direct transfer','Proof reviewed','Pending'],['Counter payment','Staff confirmed','Posted']],actions:[{id:'verify-payment',label:'Verify payment',primary:true,nextScene:1}],auto:['verify-payment']},
          {eyebrow:'Payment confirmed',title:'Order can move to operations',state:'Verified',channel:'Internal + External',caption:'Only confirmed payment releases the order into the next operational step.',kpis:[['Payment','Verified'],['Order','Released'],['Audit','Logged']],rows:[['Payment receipt','Created','Done'],['Order state','Paid','Done'],['Handoff','Ready','Next']],actions:[{id:'handoff-order',label:'Open operational handoff',primary:true,nextModule:'Operations'}],auto:['module-Operations']}
        ]},
        {tab:'Handoff',module:'Operations',scenes:[
          {eyebrow:'Operational handoff',title:profile.usesProduction?'Stock or production moves next':'Stock fulfilment moves next',state:'Released',channel:'Internal + External',caption:profile.usesProduction?'Stocked items deduct immediately while made-to-order demand can enter production under staff control.':'Procured stock is reserved/released without introducing production-only steps.',kpis:[['Stock','Checked'],['Order','Created once'],['Audit','Linked']],rows:[[profile.product,'Availability checked','Done'],[profile.usesProduction?'Production intake':'Warehouse / shop pick','Controlled','Released'],['Order history','Single source','Posted']],actions:[{id:'send-delivery',label:'Send to Delivery',primary:true,nextModule:'Delivery'}],auto:['module-Delivery']}
        ]},
        {tab:'Track',module:'Delivery',scenes:[
          {eyebrow:'Delivery assignment',title:'Assign fulfilment',state:'Ready',channel:'External',caption:'The delivery workspace receives the paid order with its quote, address and customer context intact.',kpis:[['Delivery','Quoted'],['Assignment','Ready'],['Updates','Synced']],rows:[['Order','Paid','Done'],['Address','Validated','Done'],['Rider / provider','Choose next','Next']],actions:[{id:'assign-track',label:'Assign delivery',primary:true,nextScene:1}],auto:['assign-track']},
          {eyebrow:'Customer + staff tracking',title:'Follow fulfilment to delivery',state:'On route',channel:'External',caption:'Customer tracking and staff/rider/provider updates stay synchronized to the same delivery record.',kpis:[['Delivery','Assigned'],['ETA','Visible'],['Updates','Synced']],progress:72,rows:[['Confirmed','Customer sees it','Done'],['Picked up','Staff/rider update','Done'],['Delivered','Proof + status','Next']],actions:[{id:'complete-track',label:'Complete delivery',primary:true,nextScene:2}],auto:['complete-track']},
          {eyebrow:'Delivered',title:'The order closes cleanly',state:'Delivered',channel:'External',caption:'Proof, tracking and final fulfilment state stay available to the customer and the business.',kpis:[['Status','Delivered'],['Proof','Saved'],['Customer','Updated']],rows:[['Tracking page','Delivered','Done'],['Proof of delivery','Saved','Done'],['Order history','Complete','Posted']],actions:[{id:'replay-storefront',label:'Replay storefront flow',nextModule:'Catalogue'}],auto:['module-Catalogue']}
        ]}
      ]};
    }

    function miniScene(module, profile) {
      const common = {
        inventory:{title:'Inventory',kicker:profile.usesProduction?'Inputs + finished stock':'Stock-first inventory',state:'Live',kpis:[['Primary',profile.stockMeta],['Products',profile.productMeta],['Alerts','2']],rows:[[profile.stockLabel,profile.stockMeta,'Healthy'],[profile.secondary,profile.secondaryMeta,'Low'],[profile.product,profile.productMeta,'Ready']]},
        procurement:{title:'Procurement',kicker:'Purchases + receiving',state:'Received',kpis:[['Supplier',profile.supplier],['Open POs','4'],['Due','2']],rows:[['Latest arrival',profile.stockMeta,'Received'],['Unit cost','Captured','Posted'],['Inventory movement','Automatic','Done']]},
        production: profile.usesProduction ? {title:'Production',kicker:'Recipe / formula → output',state:'Running',kpis:[['Target',profile.productionTarget],['Yield','96%'],['Active','3']],rows:[[profile.production,'76%','Running'],['Materials','Released','Done'],['Quality / output','Measured','Logged']]} : {title:'Stock-first workflow',kicker:'No production module required',state:'Procured',kpis:[['Source','Supplier'],['Stock',profile.productMeta],['Production','Not needed']],rows:[['Purchase order','Supplier stock','Received'],['Warehouse / shop','Available','Ready'],['Customer sale','Stock fulfilment','Done']]},
        sales:{title:'Sales & customers',kicker:profile.channel,state:'Active',kpis:[['Channel',profile.channel],['Orders','31'],['Credit','Tracked']],rows:[[profile.customer,profile.product,'Paid'],['Customer balance','Current','Matched'],['Fulfilment','Linked','Ready']]},
        finance:{title:'Finance & expenses',kicker:'Money trail',state:'Matched',kpis:[['Cash','Visible'],['Receivable','Tracked'],['Payable','Tracked']],rows:[['Payment received','Linked order','Posted'],['Supplier cost','Linked PO','Posted'],['Expense','Accounted','Matched']]},
        reports:{title:'Reports & backup',kicker:'Review + export',state:'Ready',kpis:[['Sales','Visible'],['Stock','Traceable'],['Exports','Ready']],rows:[['Performance','Current period','View'],['Stock movement','Full trail','Export'],['Backup','Available','Ready']]},
        delivery:{title:'Delivery operations',kicker:profile.delivery,state:'On route',kpis:[['Ready','5'],['On route','4'],['Issues','1']],rows:[['Quote','Before payment','Done'],['Assignment','Rider / provider','Active'],['Tracking','Customer-visible','Live']]},
        audit:{title:'Audit Workspace',kicker:'Read-only evidence',state:'Controlled',kpis:[['Queries','2'],['Flags','1'],['Exports','Ready']],rows:[['Activity trail','Tenant-scoped','Ready'],['Audit query','Response due','Attention'],['Evidence pack','Exportable','Ready']]},
        pos:{title:'In-premise POS',kicker:profile.counter,state:'Open',kpis:[['Product',profile.product],['Basket','3 items'],['Payment','Staff']],rows:[[profile.product,'× 1','Added'],['Channel pricing','In-premise','Matched'],['Receipt','Ready','Done']]}
      };
      return common[module] || common.inventory;
    }

    function renderMini(back, module, profile) {
      const scene = miniScene(module, profile);
      back.innerHTML = `<div class="feature-mini-head"><div><div class="feature-mini-title" id="module-preview-title">${scene.title}</div><div class="feature-mini-kicker">${scene.kicker}</div></div><span class="feature-mini-state">${scene.state}</span></div><div class="feature-mini-kpis">${scene.kpis.map(([l,v])=>`<div class="feature-mini-kpi"><small>${l}</small><strong>${v}</strong></div>`).join('')}</div><div class="feature-mini-list">${scene.rows.map(([a,b,c])=>`<div class="feature-mini-row"><strong>${a}</strong><span>${b}</span><span class="feature-mini-chip ${statusClass(c)}">${c}</span></div>`).join('')}</div><div class="feature-mini-back">This is an illustrative preview, not your business data.</div>`;
    }

    const demoRoots = [...document.querySelectorAll('[data-inprofic-demo]')];
    function configFor(root) {
      const profile = serviceProfiles[activeService];
      return root.dataset.inproficDemo === 'storefront' ? storefrontConfig(profile) : generalConfig(profile);
    }
    function initDemo(root) {
      const tabs=root.querySelector('[data-demo-tabs]'),nav=root.querySelector('[data-demo-nav]'),caption=root.querySelector('[data-demo-caption]'),eyebrow=root.querySelector('[data-demo-eyebrow]'),title=root.querySelector('[data-demo-title]'),state=root.querySelector('[data-demo-state]'),channel=root.querySelector('[data-demo-channel]'),subnav=root.querySelector('[data-demo-subnav]'),kpis=root.querySelector('[data-demo-kpis]'),panel=root.querySelector('[data-demo-panel]'),progress=root.querySelector('[data-demo-progress]'),actions=root.querySelector('[data-demo-actions]'),service=root.querySelector('[data-demo-service]'),pointer=root.querySelector('[data-demo-pointer]');
      let index=0,sceneIndex=0,buttons=[],autoTimer=null,clickTimer=null,actionTimer=null,autoTargetIndex=0,renderVersion=0,manualPauseUntil=0,hoverPaused=false;
      const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
      let inView=false;
      const motionPreference=matchMedia('(prefers-reduced-motion: reduce)');
      motionPreference.addEventListener('change',()=>{clearTimeout(autoTimer);clearTimeout(clickTimer);clearTimeout(actionTimer);pointer?.classList.remove('is-visible','is-clicking');scheduleAuto();});
      const visibilityObserver=new IntersectionObserver(entries=>{
        inView=entries[0].isIntersecting;
        if(inView)scheduleAuto(1800);
        else {clearTimeout(autoTimer);clearTimeout(clickTimer);clearTimeout(actionTimer);pointer?.classList.remove('is-visible');}
      },{threshold:.2});
      visibilityObserver.observe(root);
      document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(autoTimer);clearTimeout(clickTimer);clearTimeout(actionTimer);}else scheduleAuto(1800);});
      const currentConfig=()=>configFor(root);
      const currentStep=()=>currentConfig().steps[index];
      const currentScene=()=>currentStep()?.scenes?.[sceneIndex] || currentStep()?.scenes?.[0];
      const escapeHtml=value=>String(value??'').replace(/[&<>"]/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));
      const targetById=id=>[...root.querySelectorAll('[data-demo-target]')].find(el=>el.dataset.demoTarget===id);
      const moduleIndex=module=>currentConfig().steps.findIndex(step=>step.module===module);
      const pauseManual=()=>{manualPauseUntil=Date.now()+12000;clearTimeout(autoTimer);clearTimeout(clickTimer);clearTimeout(actionTimer);pointer?.classList.remove('is-clicking');};
      const scheduleAuto=(delay=1800)=>{clearTimeout(autoTimer);if(motionPreference.matches||!inView||document.hidden)return;autoTimer=setTimeout(runAuto,delay);};
      const canAuto=()=>inView&&!document.hidden&&!motionPreference.matches&&!hoverPaused&&!root.contains(document.activeElement)&&Date.now()>=manualPauseUntil;

      function movePointer(target,onClick){
        if(reduced||!pointer||!target)return;
        const rootRect=root.getBoundingClientRect(),targetRect=target.getBoundingClientRect();
        if(!targetRect.width||!targetRect.height)return;
        const x=Math.max(5,Math.min(root.clientWidth-32,targetRect.left-rootRect.left+Math.min(targetRect.width*.56,targetRect.width-6)));
        const y=Math.max(5,Math.min(root.clientHeight-34,targetRect.top-rootRect.top+Math.min(targetRect.height*.56,targetRect.height-6)));
        pointer.classList.add('is-visible');pointer.classList.remove('is-clicking');pointer.style.transform=`translate3d(${x}px,${y}px,0)`;
        clearTimeout(clickTimer);clearTimeout(actionTimer);
        clickTimer=setTimeout(()=>{pointer.classList.add('is-clicking');actionTimer=setTimeout(()=>{pointer.classList.remove('is-clicking');onClick?.();},250);},720);
      }

      function gotoStep(nextIndex,nextScene=0){
        const config=currentConfig(); if(!config.steps.length)return;
        index=((nextIndex%config.steps.length)+config.steps.length)%config.steps.length;
        sceneIndex=Math.max(0,Math.min(nextScene,(config.steps[index].scenes?.length||1)-1));
        renderScene();
      }
      function gotoModule(module){const next=moduleIndex(module);if(next>=0)gotoStep(next,0);}
      function gotoScene(nextScene){sceneIndex=Math.max(0,Math.min(nextScene,(currentStep().scenes?.length||1)-1));renderScene();}

      function renderSubnav(scene){
        subnav.replaceChildren();
        (scene.subnav||[]).forEach(item=>{
          const btn=document.createElement('button');btn.type='button';btn.textContent=item.label;btn.dataset.demoTarget=item.id;btn.classList.toggle('is-active',!!item.active);
          btn.addEventListener('click',()=>{if(Number.isInteger(item.nextScene))gotoScene(item.nextScene);else if(item.nextModule)gotoModule(item.nextModule);});
          subnav.appendChild(btn);
        });
      }
      function renderPanel(scene){
        if(scene.fields?.length){
          panel.innerHTML=`<div class="ip-demo-form">${scene.fields.map(field=>`<button type="button" class="ip-demo-field" data-demo-target="${escapeHtml(field.id)}"><small>${escapeHtml(field.label)}</small><strong>${escapeHtml(field.value)}</strong></button>`).join('')}</div>`;
          panel.querySelectorAll('.ip-demo-field').forEach(field=>field.addEventListener('click',()=>{field.classList.add('is-demo-focus');setTimeout(()=>field.classList.remove('is-demo-focus'),650);}));
        } else {
          panel.innerHTML=(scene.rows||[]).map(([a,b,c])=>`<div class="ip-demo-row"><strong>${escapeHtml(a)}</strong><span>${escapeHtml(b)}</span><span class="ip-demo-chip ${statusClass(c)}">${escapeHtml(c)}</span></div>`).join('');
        }
      }
      function renderActions(scene){
        actions.replaceChildren();
        (scene.actions||[]).forEach(action=>{
          const btn=document.createElement('button');btn.type='button';btn.textContent=action.label;btn.dataset.demoTarget=action.id;if(action.primary)btn.classList.add('primary');
          btn.addEventListener('click',()=>{if(Number.isInteger(action.nextScene))gotoScene(action.nextScene);else if(action.nextModule)gotoModule(action.nextModule);});
          actions.appendChild(btn);
        });
      }
      function renderScene(){
        renderVersion+=1;autoTargetIndex=0;
        const config=currentConfig(),step=config.steps[index],scene=step.scenes?.[sceneIndex]||step.scenes?.[0]; if(!scene)return;
        buttons.forEach((b,n)=>{b.classList.toggle('is-active',n===index);b.setAttribute('aria-selected',n===index?'true':'false');});
        tabs.querySelectorAll('[role=tab]').forEach((tab,i)=>{tab.setAttribute('aria-selected',String(i===index));tab.tabIndex=i===index?0:-1;});
        nav.querySelectorAll('[data-demo-module]').forEach(el=>el.classList.toggle('is-active',el.dataset.demoModule===step.module));
        caption.textContent=scene.caption||'';eyebrow.textContent=scene.eyebrow||step.module;title.textContent=scene.title||step.module;state.textContent=scene.state||'Connected';
        if(channel){channel.hidden=!scene.channel;channel.textContent=scene.channel||'';channel.classList.toggle('internal',/internal/i.test(scene.channel||''));}
        renderSubnav(scene);
        kpis.innerHTML=(scene.kpis||[]).map(([l,v])=>`<div class="ip-demo-kpi"><small>${escapeHtml(l)}</small><strong>${escapeHtml(v)}</strong></div>`).join('');
        progress.classList.toggle('is-visible',!!scene.progress);progress.setAttribute('aria-hidden',scene.progress?'false':'true');progress.firstElementChild.style.width=`${scene.progress||0}%`;
        renderPanel(scene);renderActions(scene);
        root.dataset.sceneVersion=String(renderVersion);
        scheduleAuto(2200);
      }
      function rebuild(){
        const config=currentConfig();nav.replaceChildren();tabs.replaceChildren();
        config.steps.forEach((step,i)=>{
          const navBtn=document.createElement('button');navBtn.type='button';navBtn.textContent=step.module;navBtn.dataset.demoModule=step.module;navBtn.dataset.demoTarget=`module-${step.module}`;navBtn.addEventListener('click',()=>gotoStep(i,0));nav.appendChild(navBtn);
          const tab=document.createElement('button');tab.type='button';tab.className='ip-demo-tab';tab.textContent=step.tab;tab.setAttribute('role','tab');tab.id=`${root.id}-tab-${i}`;tab.setAttribute('aria-controls',`${root.id}-panel`);tab.dataset.demoTarget=`tab-${step.module}`;tab.addEventListener('click',()=>gotoStep(i,0));tab.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const count=currentConfig().steps.length;const next=event.key==='Home'?0:event.key==='End'?count-1:(index+(event.key==='ArrowRight'?1:count-1))%count;gotoStep(next,0);tabs.querySelectorAll('[role=tab]')[next]?.focus();});tabs.appendChild(tab);
        });
        buttons=[...tabs.querySelectorAll('.ip-demo-tab')];index=Math.min(index,Math.max(0,config.steps.length-1));sceneIndex=0;renderScene();
      }
      function runAuto(){
        if(!canAuto()){scheduleAuto(900);return;}
        const scene=currentScene();if(!scene)return;
        const ids=scene.auto||[];
        if(autoTargetIndex>=ids.length){gotoStep(index+1);return;}
        const id=ids[autoTargetIndex],target=targetById(id);
        if(!target){autoTargetIndex+=1;scheduleAuto(450);return;}
        const before=renderVersion;
        movePointer(target,()=>{
          target.click();
          if(renderVersion===before){autoTargetIndex+=1;scheduleAuto(820);}
        });
      }
      function refresh(reset){if(reset){index=0;sceneIndex=0;autoTargetIndex=0;}rebuild();}

      if(service){service.value=activeService;service.addEventListener('change',()=>{activeService=service.value;localStorage.setItem('inprofic-marketing-demo-service',activeService);document.querySelectorAll('[data-demo-service]').forEach(other=>{if(other!==service)other.value=activeService;});refresh(true);window.dispatchEvent(new CustomEvent('inprofic:marketing-vertical',{detail:{vertical:activeService}}));});}
      root.addEventListener('pointerdown',event=>{if(event.isTrusted)pauseManual();},true);
      root.addEventListener('mouseenter',()=>{hoverPaused=true;clearTimeout(autoTimer);clearTimeout(clickTimer);clearTimeout(actionTimer);pointer?.classList.remove('is-visible','is-clicking');});
      root.addEventListener('mouseleave',()=>{hoverPaused=false;scheduleAuto(700);});
      root.addEventListener('focusin',event=>{if(event.isTrusted)pauseManual();});
      root.addEventListener('focusout',()=>scheduleAuto(900));
      root.__refreshDemo=refresh;refresh(true);
    }
    demoRoots.forEach(initDemo);
    document.querySelectorAll('[data-demo-service]').forEach(service=>{
      if(service.closest('[data-inprofic-demo]'))return;
      service.value=activeService;
      service.addEventListener('change',()=>{
        activeService=service.value;
        localStorage.setItem('inprofic-marketing-demo-service',activeService);
        document.querySelectorAll('[data-demo-service]').forEach(other=>{if(other!==service)other.value=activeService;});
        window.dispatchEvent(new CustomEvent('inprofic:marketing-vertical',{detail:{vertical:activeService}}));
      });
    });

    // These are illustrative records, selected with the same service as both demos.
    const workflowData={
      bakery:{item:'loaves',input:'flour',yield:110,amount:20,unit:'kg',first:30,second:20,action:'Bake to the demand. Keep every order.'},
      restaurant:{item:'portions',input:'rice',yield:100,amount:10,unit:'kg',first:30,second:20,action:'Prep together. Serve each order.'},
      general:{item:'filter units',input:'steel sheet',yield:10,amount:5,unit:'sheets',first:6,second:4,action:'Coordinate the run. Retain each customer.'},
      wholesale:{item:'detergent cartons',first:30,second:20,stock:84,action:'One stock balance. Separate trade orders.'},
      retail:{item:'pairs of sneakers',first:2,second:1,stock:38,action:'From the shelf to a verified sale.'}
    };
    const workflow=document.querySelector('[data-workflow-example]');
    const originalRows=[...document.querySelectorAll('[data-workflow-rows] article')].map(row=>row.innerHTML);
    const originalNext=document.querySelector('[data-workflow-next]')?.innerHTML;
    function updateWorkflow(){
      if(!workflow)return;
      const profile=serviceProfiles[activeService],data=workflowData[activeService],made=profile.usesProduction,total=data.first+data.second;
      workflow.dataset.vertical=activeService;
      workflow.querySelector('.order-ledger-head>span').textContent=`${profile.label} / ${made?'Shared Run':'Stock-backed sales'} example`;
      workflow.querySelector('h2').textContent=data.action;
      workflow.querySelector('caption').textContent=made?'Separate customer demand coordinated in one Shared Run':'Separate sales fulfilled from available stock';
      const cells=workflow.querySelectorAll('tbody tr');
      [data.first,data.second].forEach((qty,i)=>{cells[i].cells[0].textContent=made?['Customer order A','Customer order B'][i]:['Counter order','Online order'][i];cells[i].cells[1].textContent=`${qty} ${data.item}`;cells[i].cells[2].textContent='Separate record';});
      const foot=workflow.querySelector('tfoot tr');foot.cells[0].textContent=made?'Planned output':'Confirmed sales';foot.cells[1].textContent=`${total} ${data.item}`;foot.cells[2].textContent=made?'One Shared Run':'Stock-backed fulfilment';
      const note=workflow.querySelector('.recipe-note');note.replaceChildren();
      const values=made?['Reference recipe',`${data.amount} ${data.unit} ${data.input} → ${data.yield} ${data.item}`,'Proportional requirement',`${total} ÷ ${data.yield} × ${data.amount} = ${(total/data.yield*data.amount).toLocaleString('en',{maximumFractionDigits:3})} ${data.unit}`]:['Available stock',`${data.stock} ${data.item}`,'After confirmed sales',`${data.stock} − ${total} = ${data.stock-total} ${data.item}`];
      values.forEach((value,i)=>{const el=document.createElement(i%2?'strong':'span');el.textContent=value;note.append(el);});
      workflow.querySelector(':scope>p').textContent=made?'Approval releases the material once. Customer demand and balances stay with each order.':'Stock is reserved while checkout awaits payment. Trusted confirmation commits the sale; each customer keeps a separate receipt and balance.';
      const rows=document.querySelectorAll('[data-workflow-rows] article');
      rows.forEach((row,i)=>row.innerHTML=originalRows[i]);
      if(!made){
        rows[0].querySelector('h3').textContent='Receive the goods. Keep their cost.';
        rows[0].querySelector('p').textContent=`Receive ${data.item} directly through Procurement. Supplier arrivals update sellable stock and cost, with warning and low-stock states kept distinct.`;
        rows[1].querySelector('h3').textContent='Sell from stock. Keep the customer balance.';
        rows[1].querySelector('p').textContent=activeService==='wholesale'?'Apply trade or agreed customer prices, record credit terms and keep each unpaid sale visible until Finance records payment.':'Use shelf inventory across POS and online checkout. Keep channel pricing, quantities and each customer basket connected to the same stock records.';
        rows[1].querySelector('.workflow-area').textContent='Inventory / Sales';
      }
      const next=document.querySelector('[data-workflow-next]');
      if(next){next.innerHTML=originalNext;if(!made){next.replaceChildren();[
        ['Receive the stock.','Procurement records supplier arrivals and their cost.'],
        ['Verify the payment.','Available stock is reserved during checkout and committed after trusted payment confirmation.'],
        ['Follow the fulfilment.','Pickup or delivery follows the order, with separate customer receipts and balances.'],
        ['Correct with evidence.','Compensating reversal preserves the record and checks downstream activity.']
      ].forEach(([title,copy])=>{const li=document.createElement('li'),strong=document.createElement('strong');strong.textContent=title;li.append(strong,` ${copy}`);next.append(li);});}}
    }
    updateWorkflow();
    window.addEventListener('inprofic:marketing-vertical',updateWorkflow);

    const trustCount=document.querySelector('[data-trust-count]');
    if(trustCount){
      const target=Number(trustCount.dataset.count||0);
      const showCount=()=>{
        if(matchMedia('(prefers-reduced-motion: reduce)').matches){trustCount.textContent=target.toLocaleString();return;}
        const started=performance.now(),duration=1200;
        const tick=now=>{const progress=Math.min(1,(now-started)/duration);trustCount.textContent=Math.round(target*(1-Math.pow(1-progress,3))).toLocaleString();if(progress<1)requestAnimationFrame(tick);};
        requestAnimationFrame(tick);
      };
      if('IntersectionObserver'in window){const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){observer.disconnect();showCount();}},{threshold:.35});observer.observe(trustCount);}else showCount();
    }

    const trustLogoWindow=document.querySelector('[data-trust-logo-window]');
    const trustLogoTrack=trustLogoWindow?.querySelector('[data-trust-logo-track]');
    const trustLogoSet=trustLogoTrack?.querySelector('[data-trust-logo-set]');
    const trustLogoCopy=trustLogoTrack?.querySelector('[data-trust-logo-copy]');
    if(trustLogoWindow&&trustLogoTrack&&trustLogoSet&&trustLogoCopy){
      const sourceLogos=[...trustLogoSet.children].map(logo=>logo.cloneNode(true));
      const makeDecorative=logo=>{logo.setAttribute('aria-hidden','true');const image=logo.querySelector('img');if(image)image.alt='';return logo;};
      const buildTrustLogoLoop=()=>{
        trustLogoTrack.classList.add('is-building');
        trustLogoSet.replaceChildren(...sourceLogos.map(logo=>logo.cloneNode(true)));
        let repeats=0;
        while(trustLogoSet.scrollWidth<trustLogoWindow.clientWidth&&repeats<24){
          trustLogoSet.append(...sourceLogos.map(logo=>makeDecorative(logo.cloneNode(true))));
          repeats+=1;
        }
        trustLogoCopy.replaceChildren(...[...trustLogoSet.children].map(logo=>makeDecorative(logo.cloneNode(true))));
        requestAnimationFrame(()=>trustLogoTrack.classList.remove('is-building'));
      };
      let trustLogoWidth=Math.round(trustLogoWindow.clientWidth);
      buildTrustLogoLoop();
      if('ResizeObserver'in window)new ResizeObserver(entries=>{const width=Math.round(entries[0].contentRect.width);if(width===trustLogoWidth)return;trustLogoWidth=width;buildTrustLogoLoop();}).observe(trustLogoWindow);
      let trustResumeTimer;
      const pauseTrustLogos=()=>{clearTimeout(trustResumeTimer);trustLogoWindow.classList.add('is-paused');};
      const resumeTrustLogos=()=>{clearTimeout(trustResumeTimer);trustResumeTimer=setTimeout(()=>trustLogoWindow.classList.remove('is-paused'),1200);};
      trustLogoWindow.addEventListener('touchstart',pauseTrustLogos,{passive:true});
      trustLogoWindow.addEventListener('touchend',resumeTrustLogos,{passive:true});
      trustLogoWindow.addEventListener('touchcancel',resumeTrustLogos,{passive:true});
    }
    const titleMap = {'Inventory':'inventory','Procurement':'procurement','Production':'production','Sales & customers':'sales','Finance & expenses':'finance','Reports & backup':'reports','Delivery operations':'delivery','Audit Workspace':'audit','Dedicated in-premise POS':'pos'};
    const moduleGrid=document.querySelector('[data-module-grid]');
    const moduleOverlay=document.querySelector('[data-module-overlay]');
    const moduleDialog=moduleOverlay?.querySelector('[data-module-dialog]');
    if(moduleOverlay&&moduleOverlay.parentElement!==document.body)document.body.appendChild(moduleOverlay);
    const modulePreview=moduleOverlay?.querySelector('[data-module-preview]');
    let activeModuleCard=null;
    let moduleCloseTimer=null;

    function openModulePreview(card,module){
      if(!moduleOverlay||!moduleDialog||!modulePreview)return;
      window.clearTimeout(moduleCloseTimer);
      if(activeModuleCard&&activeModuleCard!==card)activeModuleCard.setAttribute('aria-expanded','false');
      activeModuleCard=card;
      card.setAttribute('aria-expanded','true');
      card.classList.remove('is-opening');
      void card.offsetWidth;
      card.classList.add('is-opening');
      window.setTimeout(()=>card.classList.remove('is-opening'),470);
      renderMini(modulePreview,module,serviceProfiles[activeService]);
      moduleOverlay.showModal();
    }
    function closeModulePreview(){ moduleOverlay?.close(); }
    moduleOverlay?.querySelectorAll('[data-module-close]').forEach(button=>button.addEventListener('click',closeModulePreview));
    moduleOverlay?.addEventListener('close',()=>{
      activeModuleCard?.setAttribute('aria-expanded','false'); activeModuleCard=null;
      document.body.classList.remove('module-preview-open');
    });

    const moduleCards=[...document.querySelectorAll('#capabilities .feature-card')];
    moduleCards.forEach((card,moduleCardIndex)=>{
      card.style.setProperty('--module-index',moduleCardIndex);
      const heading=card.querySelector('h3');
      const module=titleMap[heading?.textContent.trim()];
      if(!module)return;
      card.classList.add('is-flippable');
      card.dataset.featureDemo=module;
      card.tabIndex=0;
      card.setAttribute('role','button');
      card.setAttribute('aria-haspopup','dialog');
      card.setAttribute('aria-controls','module-preview-dialog');
      card.setAttribute('aria-expanded','false');
      card.setAttribute('aria-label',`${heading.textContent.trim()}: open an interactive module preview`);
      card.insertAdjacentHTML('beforeend','<span class="feature-demo-hint">Click to preview <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M7 7h10v10"/><path d="M7 17L17 7"/></svg></span>');
      const open=()=>openModulePreview(card,module);
      card.addEventListener('click',open);
      card.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();open();}});
    });

    function drawModuleConnectors(){
      const svg=moduleGrid?.querySelector('[data-module-connectors]');
      const pathLayer=svg?.querySelector('[data-module-connector-paths]');
      const hubMark=moduleGrid?.querySelector('.capability-hub-mark');
      if(!svg||!pathLayer||!hubMark)return;
      const gridRect=moduleGrid.getBoundingClientRect();
      const hubRect=hubMark.getBoundingClientRect();
      const width=moduleGrid.clientWidth;
      const height=moduleGrid.clientHeight;
      if(!width||!height)return;
      svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
      pathLayer.replaceChildren();
      const center=rect=>({x:rect.left-gridRect.left+rect.width/2,y:rect.top-gridRect.top+rect.height/2});
      const edge=(from,to,rect,inset=9)=>{
        const dx=to.x-from.x,dy=to.y-from.y;
        const halfWidth=Math.max(1,rect.width/2-inset),halfHeight=Math.max(1,rect.height/2-inset);
        const scale=1/Math.max(Math.abs(dx)/halfWidth,Math.abs(dy)/halfHeight);
        return {x:from.x+dx*scale,y:from.y+dy*scale};
      };
      const hubCenter=center(hubRect);
      moduleCards.forEach((card,index)=>{
        const cardRect=card.getBoundingClientRect();
        const cardCenter=center(cardRect);
        const start=edge(hubCenter,cardCenter,hubRect,5);
        const end=edge(cardCenter,hubCenter,cardRect,12);
        const dx=end.x-start.x,dy=end.y-start.y;
        const vertical=Math.abs(dy)>=Math.abs(dx);
        const d=vertical
          ? `M ${start.x} ${start.y} C ${start.x} ${start.y+dy*.46}, ${end.x} ${end.y-dy*.18}, ${end.x} ${end.y}`
          : `M ${start.x} ${start.y} C ${start.x+dx*.46} ${start.y}, ${end.x-dx*.18} ${end.y}, ${end.x} ${end.y}`;
        ['capability-connector-base','capability-connector-flow'].forEach(className=>{
          const path=document.createElementNS('http://www.w3.org/2000/svg','path');
          path.setAttribute('d',d);
          path.setAttribute('class',className);
          if(className==='capability-connector-flow')path.style.animationDelay=`${index*-.19}s`;
          pathLayer.appendChild(path);
        });
      });
    }
    window.requestAnimationFrame(drawModuleConnectors);
    if('ResizeObserver'in window&&moduleGrid)new ResizeObserver(()=>window.requestAnimationFrame(drawModuleConnectors)).observe(moduleGrid);
    else window.addEventListener('resize',drawModuleConnectors,{passive:true});

    window.addEventListener('inprofic:marketing-vertical',()=>{
      demoRoots.forEach(root=>root.__refreshDemo?.(true));
      if(activeModuleCard&&modulePreview)renderMini(modulePreview,activeModuleCard.dataset.featureDemo,serviceProfiles[activeService]);
    });

    const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
    const revealTargets=[...document.querySelectorAll('main > section, #capabilities .feature-card, #plans article, #faq details')];

    const aboutList=document.querySelector('.about-modules');
    if(aboutList){
      aboutList.querySelectorAll(':scope > li').forEach((li,i)=>li.style.setProperty('--i',i));
      if(reduced||!('IntersectionObserver'in window)){aboutList.classList.add('is-in');}
      else{const aboutObserver=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){aboutList.classList.add('is-in');aboutObserver.disconnect();}},{threshold:.18,rootMargin:'0px 0px -6%'});aboutObserver.observe(aboutList);}
    }
    revealTargets.forEach((el,i)=>{el.dataset.reveal='';el.style.setProperty('--reveal-delay',`${Math.min((i%6)*55,275)}ms`);});
    if(reduced||!('IntersectionObserver'in window)){revealTargets.forEach(el=>el.classList.add('is-revealed'));}else{const observer=new IntersectionObserver(entries=>entries.forEach(entry=>{if(entry.isIntersecting){entry.target.classList.add('is-revealed');observer.unobserve(entry.target);}}),{threshold:.08,rootMargin:'0px 0px -7%'});revealTargets.forEach(el=>observer.observe(el));}
  })();

(() => {
    const faq = document.getElementById('faq');
    const toggle = faq?.querySelector('[data-faq-toggle]');
    const lists = [...(faq?.querySelectorAll('[data-faq-list]') || [])];
    if (!toggle || !lists.length) return;
    const questions = [...faq.querySelectorAll('details')];
    questions.forEach(question => question.addEventListener('toggle', () => {
      if (!question.open) return;
      questions.forEach(other => { if (other !== question) other.open = false; });
    }));
    const label = toggle.querySelector('[data-faq-toggle-label]');
    const icon = toggle.querySelector('[data-faq-toggle-icon]');
    toggle.addEventListener('click', () => {
      const expanded = toggle.getAttribute('aria-expanded') !== 'true';
      toggle.setAttribute('aria-expanded', String(expanded));
      lists.forEach(list => {
        list.dataset.expanded = String(expanded);
        if (!expanded) list.querySelectorAll('details:nth-child(n+4)').forEach(item => item.open = false);
      });
      if (label) label.textContent = expanded ? 'Show fewer questions' : 'Show all questions';
      if (icon) icon.style.transform = expanded ? 'rotate(180deg)' : '';
      if (!expanded) faq.scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start'});
    });
  })();

(() => {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const cursorIcons = {
    Stock:'<path d="M4 7l8 -4l8 4l-8 4z"/><path d="M4 7v10l8 4l8 -4v-10"/><path d="M12 11v10"/>',
    Production:'<circle cx="12" cy="12" r="3"/><path d="M12 2v3m0 14v3m10 -10h-3m-14 0h-3m17.1 -7.1l-2.1 2.1m-10 10l-2.1 2.1m0 -14.2l2.1 2.1m10 10l2.1 2.1"/>',
    Sales:'<path d="M5 21v-16a2 2 0 0 1 2 -2h10a2 2 0 0 1 2 2v16l-3 -2l-2 2l-2 -2l-2 2l-2 -2l-3 2"/><path d="M9 7h6m-6 4h6m-3 4h3"/>',
    Delivery:'<path d="M3 17h2a2 2 0 1 0 4 0h6a2 2 0 1 0 4 0h2v-6l-3 -4h-5v10"/><path d="M3 5h10v12H9m-6 -8h5"/>'
  };
  document.querySelectorAll('.hero-typing').forEach((wrap) => {
    const wordEl = wrap.querySelector('.hero-typeword');
    const textEl = wrap.querySelector('[data-typed-text]');
    const iconEl = wrap.querySelector('[data-typing-icon]');
    let words = [];
    try { words = JSON.parse(wrap.dataset.words || '[]'); } catch(_) {}
    words = words.filter(Boolean);
    const setIcon = word => { if(iconEl) iconEl.innerHTML = cursorIcons[word] || cursorIcons.Stock; };
    if (!wordEl || !textEl || words.length < 2 || reduced) {
      wrap.dataset.static = '1';
      if (textEl && words[0]) textEl.textContent = words[0];
      setIcon(words[0] || 'stock');
      return;
    }
    let index = 0, deleting = false;
    let current = words[0];
    textEl.textContent = current;
    setIcon(words[0]);
    const tick = () => {
      if (matchMedia('(prefers-reduced-motion: reduce)').matches) { textEl.textContent=words[index];setIcon(words[index]);setTimeout(tick,500);return; }
      if (document.hidden) { setTimeout(tick,250); return; }
      const target = words[index];
      wrap.dataset.currentWord=target;
      if (!deleting) {
        current = target.slice(0, current.length + 1);
        textEl.textContent = current;
        if (current === target) {
          deleting = true;
          return setTimeout(tick, 1200);
        }
        return setTimeout(tick, 80);
      }
      current = target.slice(0, Math.max(0, current.length - 1));
      textEl.textContent = current;
      if (!current.length) {
        deleting = false;
        index = (index + 1) % words.length;
        setIcon(words[index]);
        return setTimeout(tick, 220);
      }
      setTimeout(tick, 45);
    };
    setTimeout(tick, 700);
  });
})();
